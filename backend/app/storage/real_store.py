"""SQLite + events.jsonl + wiki backed store. Same public methods and shapes as MockStore.

Differences from mock mode, on purpose:
- approve_draft only approves: status "approved" + approved_at, and a queue item
  kind="send" ref=<draft id>. Sending happens on the send path, never here.
- Only a "pending" draft can be approved or rejected (ValueError otherwise).
- Every user-facing write appends the matching event from docs/events.md.
"""

import json
import re
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..mock_store import PERSONA_FIELDS, NotFound
from . import db, events, queue, seed, wiki

DOC_NAMES = ("persona", "fit", "offers", "metrics")
LEGACY_LEAD_STATUSES = ("new", "useful", "maybe", "skip", "drafted", "sent", "replied", "won", "lost")
# Spec v2 §6.3 Tracker
TRACKER_STATUSES = ("discovered", "filtered", "researched", "dropped", "drafted", "verified", "ready",
                    "copied", "sent", "positive_reply", "not_interested", "no_reply", "meeting", "won", "lost")
LEAD_STATUSES = tuple(dict.fromkeys(LEGACY_LEAD_STATUSES + TRACKER_STATUSES))
# Spec v2 posting fields (columns added by db.MIGRATIONS); null on older leads
POSTING_FIELDS = ("source_posting_id", "company_slug", "department", "location", "remote", "employment_type",
                  "language", "published_at", "description", "apply_url")
VERDICTS = ("useful", "maybe", "skip")
DRAFT_STATUSES = ("pending", "approved", "sent", "rejected",
                  "ready", "needs_human", "copied", "superseded")   # spec v2: ready | needs_human -> copied
CHANNELS = ("email", "apply_link", "apply_interim", "warm_intro", "direct_note")
PIPELINE_COLUMNS = (
    ("Drafted", ("drafted",)),
    ("Sent", ("sent",)),
    ("Replied", ("replied",)),
    ("Active", ("won",)),
    ("Done", ("lost",)),
)
LEAD_FIELDS = ("id", "nickname", "title", "company", "source", "url", "fetched",
               "rate_text", "score", "why", "status", "found_at")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit].rstrip("-")


def _strip_sample(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if k != "_sample"}


def normalize_url(url: str) -> str:
    """Dedup key for a URL: lowercase scheme/host, no fragment, no trailing slash."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def empty_doc(name: str) -> dict:
    """Valid shapes for when nothing has been generated yet."""
    if name == "persona":
        return {
            "name": "", "role": "", "years": None, "location": "", "skills": [],
            "wants": "", "avoids": "", "hours_per_week": None, "income_goal_monthly": None,
            "sources": [],
        }
    if name == "fit":
        return {
            "fits": [],
            "rate": {"floor": None, "ask": None, "low": None, "high": None,
                     "currency": "USD", "unit": "hour", "reasons": []},
            "goal_checks": [],
        }
    if name == "offers":
        return {"offers": []}
    if name == "metrics":
        iso = datetime.now(timezone.utc).isocalendar()
        return {
            "week": f"{iso.year}-W{iso.week:02d}",
            "headline": [],
            "channels": [],
            "guardrails": {"unapproved_sends": 0, "below_floor_quotes": 0,
                           "unsourced_claims": 0, "duplicate_contacts": 0},
            "learning": [],
        }
    raise KeyError(name)


class RealStore:
    def __init__(self, data_dir: Path, seed_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.seed_dir = Path(seed_dir)
        seed.ensure_seeded(self.data_dir, self.seed_dir)
        self.conn = self._open()

    def _open(self) -> sqlite3.Connection:
        conn = db.connect(self.data_dir)
        db.init_schema(conn)
        return conn

    def close(self) -> None:
        self.conn.close()

    def _event(self, type: str, actor: str, subject: str, payload: dict) -> dict:
        return events.append_event(self.data_dir, type, actor, subject, payload)

    # Reset -----------------------------------------------------------------
    def reset(self) -> None:
        """Delete app.db and the events.jsonl contents, then re-seed. Only touches data_dir."""
        data = self.data_dir.resolve()
        if data == self.seed_dir.resolve():
            raise ValueError("Refusing to reset: data_dir is the seed folder")
        targets = [data / db.DB_FILENAME, data / f"{db.DB_FILENAME}-wal", data / f"{db.DB_FILENAME}-shm"]
        events_file = data / events.EVENTS_FILENAME
        for t in targets + [events_file]:
            if t.parent != data:  # paranoia: never delete outside data_dir
                raise ValueError(f"Refusing to touch {t}")
        with db.WRITE_LOCK:
            self.conn.close()
            for t in targets:
                t.unlink(missing_ok=True)
            seed_events = self.seed_dir / events.EVENTS_FILENAME
            if events_file.exists() and not events_file.is_symlink():
                with events_file.open("wb") as fh:  # truncate; reset is the one deliberate wipe
                    if seed_events.is_file():
                        fh.write(seed_events.read_bytes())
        seed.ensure_seeded(data, self.seed_dir)
        if not events_file.exists() and (self.seed_dir / events.EVENTS_FILENAME).is_file():
            shutil.copy2(self.seed_dir / events.EVENTS_FILENAME, events_file)
        self.conn = self._open()

    # Reads -----------------------------------------------------------------
    def get(self, name: str) -> dict:
        if name in DOC_NAMES:
            return self._doc(name)
        if name == "state":
            return self._state()
        if name == "leads":
            return self.leads()
        if name == "drafts":
            return self.drafts()
        if name == "pipeline":
            return self._pipeline()
        raise KeyError(name)

    def _doc(self, name: str) -> dict:
        out = empty_doc(name)
        row = self.conn.execute("SELECT json FROM docs WHERE name = ?", (name,)).fetchone()
        if row:
            stored = _strip_sample(json.loads(row["json"]))
            if name == "fit" and isinstance(stored.get("rate"), dict):
                stored["rate"] = {**out["rate"], **stored["rate"]}
            if name == "metrics" and isinstance(stored.get("guardrails"), dict):
                stored["guardrails"] = {**out["guardrails"], **stored["guardrails"]}
            out.update(stored)
        if name == "fit" and out["rate"].get("floor") is None:
            out["rate"]["floor"] = self._goal_floor()
        return out

    def _goal_floor(self) -> int | float | None:
        """The rate floor (EUR/hour) from the approved profile, which owns it (CLAUDE.md rule 2)."""
        from ..profiles import rate_floor  # lazy: profiles imports this package

        value = rate_floor(self)
        if value is None:
            return None
        return int(value) if float(value).is_integer() else value

    def _state(self) -> dict:
        persona = self._doc("persona")
        bits = [persona.get("role") or ""]
        if persona.get("years") is not None:
            bits.append(f"{persona['years']} yrs")
        bits.append(persona.get("location") or "")
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        week_ahead = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        headline = " · ".join(b for b in bits if b)
        if not headline:  # spec v2: the approved profile has skills, not a role line
            from ..profiles import approved_profile  # lazy: profiles imports this package

            approved = approved_profile(self) or {}
            headline = " · ".join(s["name"] for s in approved.get("skills", [])[:3])
        return {
            "profile": {"name": persona.get("name") or "", "headline": headline},
            "today": {
                "new_leads": q("SELECT COUNT(*) FROM leads WHERE status = 'new'"),
                "drafts_pending": q("SELECT COUNT(*) FROM drafts WHERE status = 'pending'"),
                "deadlines_this_week": q(
                    "SELECT COUNT(*) FROM deadlines WHERE done = 0 AND due_at IS NOT NULL AND due_at < ?",
                    week_ahead,
                ),
                "replies_unread": q("SELECT COUNT(*) FROM replies WHERE label IS NULL"),
            },
            "last_checkin_at": self.get_setting("last_checkin_at"),
            "mode": "real",
        }

    def _pipeline(self) -> dict:
        now = datetime.now(timezone.utc)
        columns = []
        for col_name, statuses in PIPELINE_COLUMNS:
            marks = ",".join("?" * len(statuses))
            rows = self.conn.execute(
                f"SELECT * FROM leads WHERE status IN ({marks}) ORDER BY found_at DESC, id", statuses
            ).fetchall()
            columns.append({"name": col_name, "items": [
                {"nickname": r["nickname"], "note": self._pipeline_note(r, col_name, now)} for r in rows
            ]})
        deadlines = self.conn.execute(
            "SELECT nickname, what, due_at FROM deadlines WHERE done = 0 ORDER BY due_at, id"
        ).fetchall()
        return {
            "columns": columns,
            "deadlines": [
                {"nickname": d["nickname"], "what": d["what"], "due": (d["due_at"] or "")[:10]} for d in deadlines
            ],
        }

    def _pipeline_note(self, lead: sqlite3.Row, column: str, now: datetime) -> str:
        lead_id = lead["id"]
        if column == "Drafted":
            row = self.conn.execute(
                "SELECT status FROM drafts WHERE lead_id = ? ORDER BY created_at DESC LIMIT 1", (lead_id,)
            ).fetchone()
            if row and row["status"] == "approved":
                return "Approved, waiting to send"
            return "Waiting for approval"
        if column == "Sent":
            row = self.conn.execute(
                "SELECT sent_at FROM drafts WHERE lead_id = ? AND sent_at IS NOT NULL "
                "ORDER BY sent_at DESC LIMIT 1", (lead_id,)
            ).fetchone()
            sent = _parse_ts(row["sent_at"]) if row else None
            if sent:
                days = max(0, (now - sent).days)
                return f"No reply, {days} day{'s' if days != 1 else ''}"
            return "No reply yet"
        if column == "Replied":
            row = self.conn.execute(
                "SELECT label FROM replies WHERE lead_id = ? ORDER BY received_at DESC LIMIT 1", (lead_id,)
            ).fetchone()
            return f"Reply: {row['label']}" if row and row["label"] else "Reply received"
        if column == "Active":
            row = self.conn.execute(
                "SELECT what, due_at FROM deadlines WHERE lead_id = ? AND done = 0 ORDER BY due_at LIMIT 1",
                (lead_id,),
            ).fetchone()
            parts = [lead["rate_text"] or ""]
            if row:
                parts.append(f"{row['what']} due {(row['due_at'] or '')[:10]}".strip())
            return " · ".join(p for p in parts if p)
        return lead["verdict_note"] or ""

    # Docs and settings -----------------------------------------------------
    def set_doc(self, name: str, obj: dict) -> dict:
        if name not in DOC_NAMES:
            raise ValueError(f"Unknown doc {name!r}; use one of {', '.join(DOC_NAMES)}")
        if not isinstance(obj, dict):
            raise ValueError("Doc must be a JSON object")
        with db.WRITE_LOCK, self.conn:
            self._put_doc(name, _strip_sample(obj))
        return self._doc(name)

    def _put_doc(self, name: str, obj: dict) -> None:
        self.conn.execute(
            "INSERT INTO docs (name, json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET json = excluded.json, updated_at = excluded.updated_at",
            (name, json.dumps(obj, ensure_ascii=False), _now()),
        )

    def get_setting(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        with db.WRITE_LOCK, self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # Persona ---------------------------------------------------------------
    def update_persona(self, fields: dict) -> dict:
        changes = {k: v for k, v in fields.items() if k in PERSONA_FIELDS}
        with db.WRITE_LOCK, self.conn:
            persona = self._doc("persona")
            persona.update(changes)
            self._put_doc("persona", persona)
        self._event("persona_updated", "user", "persona", {"fields": changes})
        return self._doc("persona")

    def add_cv(self, filename: str, content: bytes) -> dict:
        """Save the file to raw/ (never overwriting) and list it in persona.sources."""
        source = wiki.save_raw(self.data_dir, filename, content)
        with db.WRITE_LOCK, self.conn:
            persona = self._doc("persona")
            if source not in persona["sources"]:
                persona["sources"].append(source)
            self._put_doc("persona", persona)
        self._event("persona_updated", "user", "persona", {"source": source, "filename": Path(filename).name})
        return self._doc("persona")

    # Leads -----------------------------------------------------------------
    @staticmethod
    def _lead_out(row: sqlite3.Row) -> dict:
        lead = {k: row[k] for k in LEAD_FIELDS}
        lead["fetched"] = bool(lead["fetched"])
        if row["source_posting_id"]:
            lead.update({k: row[k] for k in POSTING_FIELDS})
            lead["remote"] = None if row["remote"] is None else bool(row["remote"])
            lead["skills"] = json.loads(row["skills_json"]) if row["skills_json"] else []
            if row["research_error"]:
                lead["research_error"] = row["research_error"]
        if row["verdict_note"]:
            lead["verdict_note"] = row["verdict_note"]
        return lead

    def _lead_row(self, lead_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            raise NotFound(f"No lead with id {lead_id}")
        return row

    def leads(self, status: str | None = None) -> dict:
        # Ranked: unscored (just pasted) first, as mock mode does, then by score
        sql = "SELECT * FROM leads"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY score IS NOT NULL, score DESC, published_at DESC, found_at DESC, id"
        leads = [self._lead_out(r) for r in self.conn.execute(sql, args).fetchall()]
        briefs = {r["lead_id"]: json.loads(r["json"]) for r in self.conn.execute("SELECT lead_id, json FROM briefs")}
        for lead in leads:
            if lead["id"] in briefs:
                lead["brief"] = briefs[lead["id"]]
        return {"leads": leads}

    # Researcher (spec v2 §5.3) -----------------------------------------------
    def lead(self, lead_id: str) -> dict:
        lead = self._lead_out(self._lead_row(lead_id))
        row = self.conn.execute("SELECT json FROM briefs WHERE lead_id = ?", (lead_id,)).fetchone()
        if row:
            lead["brief"] = json.loads(row["json"])
        return lead

    def leads_to_research(self, limit: int, services: list[dict] | None = None) -> list[dict]:
        """The "filtered" leads most relevant to the approved services (app/relevance.py), saving each
        lead's relevance as its score. Leads whose research failed before go last."""
        from ..relevance import rank  # lazy: keeps storage free of product logic imports at load

        rows = self.conn.execute(
            "SELECT * FROM leads WHERE status = 'filtered' AND COALESCE(company_slug, lower(company)) NOT IN "
            "(SELECT COALESCE(company_slug, lower(company)) FROM leads WHERE status = 'not_interested')").fetchall()
        leads = [self._lead_out(r) | {"_failed": bool(r["research_error"])} for r in rows]
        ranked = rank(leads, services or []) if services else [(l, l.get("score") or 0, None) for l in leads]
        with db.WRITE_LOCK, self.conn:
            self.conn.executemany("UPDATE leads SET score = ? WHERE id = ?", [(sc, l["id"]) for l, sc, _ in ranked])
        ranked.sort(key=lambda x: x[0]["_failed"])  # stable: keeps relevance order within each group
        out = []
        for lead, sc, _ in ranked[: int(limit)]:
            lead.pop("_failed", None)
            out.append({**lead, "score": sc})
        return out

    def save_brief(self, lead_id: str, brief: dict, status: str) -> dict:
        if status not in ("researched", "dropped"):
            raise ValueError(f"Bad research status {status!r}")
        with db.WRITE_LOCK, self.conn:
            row = self._lead_row(lead_id)
            self.conn.execute("INSERT OR REPLACE INTO briefs (lead_id, json, created_at) VALUES (?, ?, ?)",
                              (lead_id, json.dumps(brief, ensure_ascii=False), _now()))
            self.conn.execute("UPDATE leads SET status = ?, why = ?, research_error = NULL WHERE id = ?",
                              (status, brief.get("fit_reason") or row["why"], lead_id))
        self._event("lead_researched" if status == "researched" else "lead_dropped", "agent", f"lead:{row['nickname']}",
                    {"lead_id": lead_id, "service_id": brief.get("service_id"), "channel": brief.get("channel"),
                     "hook_type": (brief.get("hook") or {}).get("type"), "reason": brief.get("fit_reason")})
        return self.lead(lead_id)

    def save_research_error(self, lead_id: str, error: str, cost: dict) -> None:
        with db.WRITE_LOCK, self.conn:
            self.conn.execute("UPDATE leads SET research_error = ? WHERE id = ?", (error[:500], lead_id))

    def set_verdict(self, lead_id: str, verdict: str, note: str | None) -> dict:
        if verdict not in VERDICTS:
            raise ValueError(f"Verdict must be one of {', '.join(VERDICTS)}")
        with db.WRITE_LOCK, self.conn:
            row = self._lead_row(lead_id)
            if note:
                self.conn.execute("UPDATE leads SET status = ?, verdict_note = ? WHERE id = ?",
                                  (verdict, note, lead_id))
            else:
                self.conn.execute("UPDATE leads SET status = ? WHERE id = ?", (verdict, lead_id))
        payload = {"lead_id": lead_id, "verdict": verdict, "previous": row["status"]}
        if note:
            payload["note"] = note
        self._event("lead_verdict", "user", f"lead:{row['nickname']}", payload)
        return self._lead_out(self._lead_row(lead_id))

    def _unique_nickname(self, base: str) -> str:
        base = _slug(base) or "lead"
        name, n = base, 2
        while self.conn.execute("SELECT 1 FROM leads WHERE nickname = ?", (name,)).fetchone():
            name, n = f"{base}-{n}", n + 1
        return name

    def add_seed(self, url: str) -> dict:
        """Add a pasted link as a new, unscored lead. A link already known returns the existing lead."""
        key = normalize_url(url)
        with db.WRITE_LOCK, self.conn:
            existing = self.conn.execute("SELECT * FROM leads WHERE dedup_key = ?", (key,)).fetchone()
            if existing is None:
                lead_id = _new_id("ld")
                self.conn.execute(
                    "INSERT INTO leads (id, nickname, title, company, source, url, fetched, rate_text, score, "
                    "why, status, found_at, dedup_key) VALUES (?, ?, ?, NULL, 'seed', ?, 0, NULL, NULL, ?, 'new', ?, ?)",
                    (lead_id, self._unique_nickname("pasted-link"), "Pasted link (not fetched yet)", url,
                     "Waiting to be fetched and scored", _now(), key),
                )
            else:
                lead_id = existing["id"]
        lead = self._lead_out(self._lead_row(lead_id))
        payload = {"url": url, "lead_id": lead_id}
        if existing is not None:
            payload["duplicate"] = True
        self._event("seed_added", "user", f"lead:{lead['nickname']}", payload)
        return lead

    def upsert_leads(self, leads: list[dict]) -> list[dict]:
        """Insert new leads; skip any whose dedup_key (default: normalized url, else company+title) exists.

        Returns only the leads that were inserted.
        """
        inserted_ids = []
        with db.WRITE_LOCK, self.conn:
            for item in leads:
                key = item.get("dedup_key")
                if not key:
                    if item.get("url"):
                        key = normalize_url(item["url"])
                    else:
                        key = f"{_slug(item.get('company') or '')}|{_slug(item.get('title') or '', 120)}"
                if self.conn.execute("SELECT 1 FROM leads WHERE dedup_key = ?", (key,)).fetchone():
                    continue
                posting_id = item.get("source_posting_id")
                if posting_id and self.conn.execute(
                        "SELECT 1 FROM leads WHERE source_posting_id = ?", (posting_id,)).fetchone():
                    continue
                status = item.get("status") or "new"
                if status not in LEAD_STATUSES:
                    raise ValueError(f"Unknown lead status {status!r}")
                lead_id = item.get("id") or _new_id("ld")
                if self.conn.execute("SELECT 1 FROM leads WHERE id = ?", (lead_id,)).fetchone():
                    lead_id = _new_id("ld")
                score = item.get("score")
                self.conn.execute(
                    "INSERT INTO leads (id, nickname, title, company, source, url, fetched, rate_text, score, "
                    "why, status, found_at, dedup_key, verdict_note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        lead_id,
                        self._unique_nickname(item.get("nickname") or item.get("title") or "lead"),
                        item.get("title"), item.get("company"), item.get("source"), item.get("url"),
                        1 if item.get("fetched") else 0, item.get("rate_text"),
                        None if score is None else int(score),
                        item.get("why"), status, item.get("found_at") or _now(), key, item.get("verdict_note"),
                    ),
                )
                if posting_id:
                    remote = item.get("remote")
                    values = [item.get(k) for k in POSTING_FIELDS]
                    values[POSTING_FIELDS.index("remote")] = None if remote is None else int(bool(remote))
                    sets = ", ".join(f"{k} = ?" for k in POSTING_FIELDS)
                    self.conn.execute(f"UPDATE leads SET {sets}, skills_json = ? WHERE id = ?",
                                      (*values, json.dumps(item.get("skills") or []), lead_id))
                inserted_ids.append(lead_id)
        out = [self._lead_out(self._lead_row(i)) for i in inserted_ids]
        for lead in out:
            self._event("lead_found", "agent", f"lead:{lead['nickname']}",
                        {"lead_id": lead["id"], "source": lead["source"], "url": lead["url"], "score": lead["score"]})
        return out

    # Drafts ----------------------------------------------------------------
    @staticmethod
    def _draft_out(row: sqlite3.Row) -> dict:
        verifier = json.loads(row["verifier_json"]) if row["verifier_json"] else None
        draft = {
            "id": row["id"], "lead_id": row["lead_id"], "nickname": row["nickname"],
            "channel": row["channel"], "to": row["to_addr"], "subject": row["subject"], "body": row["body"],
            "verifier": verifier or {"passed": False, "checks": []},
            "status": row["status"], "created_at": row["created_at"],
        }
        for extra in ("approved_at", "sent_at", "reject_note", "language", "round", "forwardable_blurb",
                      "hook_source_url", "service_id", "final_text", "copied_at", "kind"):
            if row[extra] is not None:
                draft[extra] = row[extra]
        if row["claims_json"]:
            draft["claims_used"] = json.loads(row["claims_json"])
        return draft

    def _draft_row(self, draft_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise NotFound(f"No draft with id {draft_id}")
        return row

    @staticmethod
    def _subject(row: sqlite3.Row) -> str:
        return f"lead:{row['nickname']}" if row["nickname"] else f"draft:{row['id']}"

    def drafts(self, status: str | None = None) -> dict:
        sql = "SELECT * FROM drafts"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY created_at DESC, id"
        return {"drafts": [self._draft_out(r) for r in self.conn.execute(sql, args).fetchall()]}

    def add_draft(self, draft: dict) -> dict:
        """Store a new draft (status pending by default) and move its lead to "drafted"."""
        channel = draft.get("channel") or "email"
        if channel not in CHANNELS:
            raise ValueError(f"Unknown channel {channel!r}")
        status = draft.get("status") or "pending"
        if status not in DRAFT_STATUSES:
            raise ValueError(f"Unknown draft status {status!r}")
        lead_id = draft.get("lead_id")
        draft_id = draft.get("id") or _new_id("dr")
        with db.WRITE_LOCK, self.conn:
            nickname = draft.get("nickname")
            if lead_id:
                lead = self._lead_row(lead_id)
                nickname = nickname or lead["nickname"]
                if lead["status"] in ("new", "useful", "maybe"):
                    self.conn.execute("UPDATE leads SET status = 'drafted' WHERE id = ?", (lead_id,))
            if self.conn.execute("SELECT 1 FROM drafts WHERE id = ?", (draft_id,)).fetchone():
                raise ValueError(f"Draft {draft_id} already exists")
            self.conn.execute(
                "INSERT INTO drafts (id, lead_id, nickname, channel, to_addr, subject, body, template_version, "
                "verifier_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    draft_id, lead_id, nickname, channel, draft.get("to", draft.get("to_addr")),
                    draft.get("subject"), draft.get("body") or "", draft.get("template_version"),
                    json.dumps(draft["verifier"]) if draft.get("verifier") is not None else None,
                    status, draft.get("created_at") or _now(),
                ),
            )
            row = self._draft_row(draft_id)
        self._event("draft_created", "agent", self._subject(row),
                    {"draft_id": draft_id, "lead_id": lead_id, "template": row["template_version"]})
        return self._draft_out(row)

    # Spec v2 drafts ------------------------------------------------------
    def save_v2_draft(self, d: dict) -> dict:
        """Store a Writer/Verifier draft. Earlier open drafts for the same lead and language are
        superseded; the lead moves to "ready" (passed) or "drafted" (needs the freelancer's review)."""
        if d["status"] not in ("ready", "needs_human") or d["channel"] not in CHANNELS:
            raise ValueError("bad v2 draft")
        draft_id = _new_id("dr")
        with db.WRITE_LOCK, self.conn:
            lead = self._lead_row(d["lead_id"])
            kind = d.get("kind") or "initial"
            self.conn.execute("UPDATE drafts SET status = 'superseded' WHERE lead_id = ? AND language = ? "
                              "AND COALESCE(kind, 'initial') = ? AND status IN ('ready', 'needs_human')",
                              (d["lead_id"], d["language"], kind))
            self.conn.execute(
                "INSERT INTO drafts (id, lead_id, nickname, channel, to_addr, subject, body, template_version, "
                "verifier_json, status, created_at, language, round, forwardable_blurb, claims_json, "
                "hook_source_url, service_id, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (draft_id, d["lead_id"], lead["nickname"], d["channel"], lead["apply_url"] or lead["url"],
                 d.get("subject"), d["body"], d.get("template_version"), json.dumps(d["verifier"], ensure_ascii=False),
                 d["status"], _now(), d["language"], d["round"], d.get("forwardable_blurb"),
                 json.dumps(d.get("claims_used") or [], ensure_ascii=False), d.get("hook_source_url"), d.get("service_id"),
                 kind),
            )
            if kind == "initial" and lead["status"] in ("researched", "drafted", "verified", "ready"):
                self.conn.execute("UPDATE leads SET status = ? WHERE id = ?",
                                  ("ready" if d["status"] == "ready" else "drafted", d["lead_id"]))
            row = self._draft_row(draft_id)
        subject = self._subject(row)
        self._event("draft_created", "agent", subject, {"draft_id": draft_id, "lead_id": d["lead_id"],
                                                        "template": d.get("template_version"), "language": d["language"],
                                                        "rounds": d["round"], "status": d["status"]})
        if d["status"] == "needs_human":
            self._event("verifier_failed", "agent", subject, {"draft_id": draft_id,
                                                              "failed": [c["name"] for c in d["verifier"]["checks"] if not c["passed"]]})
        return self._draft_out(row)

    def copy_draft(self, draft_id: str, final_text: str) -> dict:
        """The freelancer copied the draft to send it themselves: their approval (spec v2 §2.1)."""
        if not isinstance(final_text, str) or not final_text.strip():
            raise ValueError("final_text is required")
        with db.WRITE_LOCK, self.conn:
            row = self._draft_row(draft_id)
            if row["status"] not in ("ready", "needs_human", "copied"):
                raise ValueError(f"Draft {draft_id} is {row['status']}; it can't be copied")
            self.conn.execute("UPDATE drafts SET status = 'copied', final_text = ?, copied_at = COALESCE(copied_at, ?) "
                              "WHERE id = ?", (final_text, _now(), draft_id))
            if row["lead_id"]:
                self.conn.execute("UPDATE leads SET status = 'copied' WHERE id = ? AND status IN "
                                  "('researched', 'drafted', 'verified', 'ready')", (row["lead_id"],))
        subject = self._subject(row)
        edited = final_text.strip() != (row["body"] or "").strip()
        if edited:
            self._event("draft_edited", "user", subject, {"draft_id": draft_id, "before": row["body"],
                                                          "after": final_text, "template": row["template_version"]})
        self._event("draft_copied", "user", subject, {"draft_id": draft_id, "lead_id": row["lead_id"],
                                                      "channel": row["channel"], "language": row["language"],
                                                      "edited": edited, "passed_verifier": row["status"] == "ready"})
        return self._draft_out(self._draft_row(draft_id))

    def leads_to_draft(self, limit: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT l.id FROM leads l WHERE l.status = 'researched' AND COALESCE(l.company_slug, lower(l.company)) "
            "NOT IN (SELECT COALESCE(company_slug, lower(company)) FROM leads WHERE status = 'not_interested') AND NOT EXISTS "
            "(SELECT 1 FROM drafts d WHERE d.lead_id = l.id AND d.status != 'superseded') "
            "ORDER BY l.score DESC, l.published_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [self.lead(r["id"]) for r in rows]

    def approve_draft(self, draft_id: str, final_text: str) -> dict:
        """Approve (not send): status "approved", approved_at, and queue kind="send" ref=draft_id."""
        if not isinstance(final_text, str) or not final_text.strip():
            raise ValueError("final_text is required")
        with db.WRITE_LOCK, self.conn:
            row = self._draft_row(draft_id)
            if row["status"] != "pending":
                raise ValueError(f"Draft {draft_id} is {row['status']}; only a pending draft can be approved")
            self.conn.execute(
                "UPDATE drafts SET body = ?, status = 'approved', approved_at = ? WHERE id = ?",
                (final_text, _now(), draft_id),
            )
            queue_id = queue.enqueue_unlocked(self.conn, "send", draft_id)
        subject = self._subject(row)
        if final_text != row["body"]:
            self._event("draft_edited", "user", subject, {
                "draft_id": draft_id, "before": row["body"], "after": final_text,
                "template": row["template_version"],
            })
        self._event("draft_approved", "user", subject, {"draft_id": draft_id, "queue_id": queue_id})
        return self._draft_out(self._draft_row(draft_id))

    def reject_draft(self, draft_id: str, note: str) -> dict:
        with db.WRITE_LOCK, self.conn:
            row = self._draft_row(draft_id)
            if row["status"] not in ("pending", "ready", "needs_human"):
                raise ValueError(f"Draft {draft_id} is {row['status']}; only an open draft can be rejected")
            self.conn.execute("UPDATE drafts SET status = 'rejected', reject_note = ? WHERE id = ?",
                              (note or "", draft_id))
        payload = {"draft_id": draft_id}
        if note:
            payload["note"] = note
        self._event("draft_rejected", "user", self._subject(row), payload)
        return self._draft_out(self._draft_row(draft_id))
