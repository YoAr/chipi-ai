"""Tracker (spec v2 §6.3): lead statuses, one-click outcomes, reminders, the company stop, the funnel.

Deterministic code; no model calls (the follow-up draft goes through the Writer and Verifier).

    discovered -> filtered -> researched -> (dropped | drafted) -> verified -> ready -> copied -> sent
      -> positive_reply | not_interested | no_reply -> meeting -> won | lost

- The freelancer moves a lead with one-click actions; only the transitions below are allowed.
- "sent" sets a reminder: after REMINDER_BUSINESS_DAYS business days, "Did they reply?", with a
  pre-written follow-up offered (max one per lead).
- "not_interested" stops all drafts for that company: open drafts are closed, other open leads at
  the company are dropped, and the Researcher and Writer refuse the company from then on.
- Every change is a `lead_status_changed` event (append-only): the Learner's outreach data.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from .mock_store import NotFound
from .storage import db
from .storage.events import append_event

REMINDER_BUSINESS_DAYS = 5
REMINDER_WHAT = "Did they reply?"
OPEN_STATUSES = ("discovered", "filtered", "researched", "drafted", "verified", "ready")

# Allowed one-click moves (from -> to). "sent" is also allowed straight from a draft stage, for
# freelancers who sent without pressing Copy.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "drafted": ("sent", "dropped"),
    "verified": ("sent", "dropped"),
    "ready": ("sent", "dropped"),
    "copied": ("sent", "ready"),
    "sent": ("positive_reply", "not_interested", "no_reply"),
    "no_reply": ("positive_reply", "not_interested", "sent"),     # late reply, or a follow-up sent
    "positive_reply": ("meeting", "won", "lost", "not_interested"),
    "meeting": ("won", "lost"),
    "researched": ("dropped",),
    "filtered": ("dropped",),
}
OUTCOMES = ("positive_reply", "not_interested", "no_reply")
CLOSES_REMINDER = ("positive_reply", "not_interested", "meeting", "won", "lost", "dropped")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def add_business_days(start: datetime, days: int) -> datetime:
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def stopped_companies(store) -> set[str]:
    """Company slugs (or names) where a lead was marked not interested."""
    rows = store.conn.execute(
        "SELECT DISTINCT COALESCE(company_slug, lower(company)) AS k FROM leads WHERE status = 'not_interested'").fetchall()
    return {r["k"] for r in rows if r["k"]}


def company_key(lead: dict) -> str | None:
    return lead.get("company_slug") or (lead.get("company") or "").lower() or None


def is_stopped(store, lead: dict) -> bool:
    return company_key(lead) in stopped_companies(store)


def _context(store, lead_id: str) -> dict:
    """What the Learner and insights need with every outcome."""
    brief = store.lead(lead_id).get("brief") or {}
    draft = store.conn.execute(
        "SELECT channel, language, id FROM drafts WHERE lead_id = ? AND status = 'copied' "
        "ORDER BY copied_at DESC LIMIT 1", (lead_id,)).fetchone()
    return {"service_id": brief.get("service_id"), "hook_type": (brief.get("hook") or {}).get("type"),
            "channel": (draft["channel"] if draft else brief.get("channel")),
            "language": (draft["language"] if draft else brief.get("draft_language")),
            "draft_id": draft["id"] if draft else None}


def set_status(store, lead_id: str, to: str, note: str | None = None, now: datetime | None = None,
               business_days: int = REMINDER_BUSINESS_DAYS) -> dict:
    now = now or _now()
    lead = store.lead(lead_id)
    frm = lead["status"]
    if to not in TRANSITIONS.get(frm, ()):
        allowed = ", ".join(TRANSITIONS.get(frm, ())) or "none"
        raise ValueError(f"Can't move a lead from {frm} to {to} (allowed: {allowed})")
    with db.WRITE_LOCK, store.conn:
        store.conn.execute("UPDATE leads SET status = ?, verdict_note = COALESCE(?, verdict_note) WHERE id = ?",
                           (to, note, lead_id))
        # A real outcome closes the reminder. "no_reply" keeps it open: that's when the follow-up is offered.
        if to in CLOSES_REMINDER:
            store.conn.execute("UPDATE deadlines SET done = 1 WHERE lead_id = ? AND what = ? AND done = 0",
                               (lead_id, REMINDER_WHAT))
        if to == "sent":
            store.conn.execute("UPDATE deadlines SET done = 1 WHERE lead_id = ? AND what = ? AND done = 0",
                               (lead_id, REMINDER_WHAT))
            store.conn.execute(
                "INSERT INTO deadlines (id, lead_id, nickname, what, due_at, done) VALUES (?, ?, ?, ?, ?, 0)",
                (f"dl_{secrets.token_hex(6)}", lead_id, lead["nickname"], REMINDER_WHAT,
                 _iso(add_business_days(now, business_days))))
            store.conn.execute("UPDATE drafts SET sent_at = COALESCE(sent_at, ?) WHERE lead_id = ? AND status = 'copied'",
                               (_iso(now), lead_id))
    stopped = []
    if to == "not_interested":
        stopped = _stop_company(store, lead, now)
    payload = {"lead_id": lead_id, "from": frm, "to": to, **_context(store, lead_id),
               "company": lead.get("company"), "company_slug": lead.get("company_slug")}
    if note:
        payload["note"] = note
    if stopped:
        payload["stopped_leads"] = stopped
    append_event(store.data_dir, "lead_status_changed", "user", f"lead:{lead['nickname']}", payload)
    return store.lead(lead_id)


def _stop_company(store, lead: dict, now: datetime) -> list[str]:
    key = company_key(lead)
    with db.WRITE_LOCK, store.conn:
        rows = store.conn.execute(
            f"SELECT id FROM leads WHERE id != ? AND COALESCE(company_slug, lower(company)) = ? "
            f"AND status IN ({','.join('?' * len(OPEN_STATUSES))})", (lead["id"], key, *OPEN_STATUSES)).fetchall()
        ids = [r["id"] for r in rows]
        for i in ids:
            store.conn.execute("UPDATE leads SET status = 'dropped', why = ? WHERE id = ?",
                               (f"{lead.get('company')} said they're not interested", i))
        store.conn.execute(
            "UPDATE drafts SET status = 'rejected', reject_note = 'company not interested' WHERE status IN "
            "('ready', 'needs_human', 'pending') AND lead_id IN (SELECT id FROM leads WHERE "
            "COALESCE(company_slug, lower(company)) = ?)", (key,))
    return ids


def reminders(store, now: datetime | None = None, include_upcoming: bool = False) -> list[dict]:
    """Due "Did they reply?" reminders (or all open ones), with the lead and its follow-up state."""
    now = now or _now()
    sql = "SELECT * FROM deadlines WHERE what = ? AND done = 0"
    args: list = [REMINDER_WHAT]
    if not include_upcoming:
        sql += " AND due_at <= ?"
        args.append(_iso(now))
    out = []
    for d in store.conn.execute(sql + " ORDER BY due_at", args).fetchall():
        try:
            lead = store.lead(d["lead_id"])
        except NotFound:
            continue
        follow = store.conn.execute(
            "SELECT id, status FROM drafts WHERE lead_id = ? AND kind = 'followup' AND status != 'superseded' "
            "ORDER BY created_at DESC LIMIT 1", (lead["id"],)).fetchone()
        out.append({"id": d["id"], "due_at": d["due_at"], "due": d["due_at"] <= _iso(now), "what": d["what"],
                    "lead": {k: lead.get(k) for k in ("id", "title", "company", "status", "url")},
                    "followup": dict(follow) if follow else None,
                    "followup_allowed": followup_allowed(store, lead)})
    return out


def followup_allowed(store, lead: dict) -> bool:
    """Max one follow-up per lead, only after sending, never for a stopped company."""
    if lead["status"] not in ("sent", "no_reply") or is_stopped(store, lead):
        return False
    n = store.conn.execute("SELECT COUNT(*) FROM drafts WHERE lead_id = ? AND kind = 'followup' AND status = 'copied'",
                           (lead["id"],)).fetchone()[0]
    return n == 0


FUNNEL = ("discovered", "filtered", "researched", "drafted", "copied", "sent", "replied", "positive_reply",
          "meeting", "won")
# A lead at a later status has passed every earlier stage
_REACHED = {
    "filtered": 1, "researched": 2, "dropped": 2, "drafted": 3, "verified": 3, "ready": 3, "copied": 4,
    "sent": 5, "no_reply": 5, "not_interested": 6, "positive_reply": 7, "meeting": 8, "won": 9, "lost": 7,
}


def funnel(store) -> dict:
    """Counts per stage, reply rates and the logging rate (spec §6.3, §10), from leads and drafts."""
    rows = store.conn.execute("SELECT status, COUNT(*) AS n FROM leads WHERE source_posting_id IS NOT NULL "
                              "GROUP BY status").fetchall()
    counts = {s: 0 for s in FUNNEL}
    for r in rows:
        reached = _REACHED.get(r["status"], 0)
        for i, stage in enumerate(FUNNEL):
            if i <= reached:
                counts[stage] += r["n"]
    by_status = {r["status"]: r["n"] for r in rows}
    sent = counts["sent"]
    copied_drafts = store.conn.execute("SELECT COUNT(DISTINCT lead_id) FROM drafts WHERE status = 'copied'").fetchone()[0]
    logged = sum(by_status.get(s, 0) for s in ("sent", "no_reply", "not_interested", "positive_reply", "meeting", "won", "lost"))
    outcomes = sum(by_status.get(s, 0) for s in ("no_reply", "not_interested", "positive_reply", "meeting", "won", "lost"))
    return {
        "stages": [{"stage": s, "count": counts[s]} for s in FUNNEL],
        "by_status": by_status,
        "rates": {
            "positive_reply_rate": round(counts["positive_reply"] / sent, 3) if sent else None,
            "reply_rate": round(counts["replied"] / sent, 3) if sent else None,
            "logging_rate": round(min(1.0, logged / copied_drafts), 3) if copied_drafts else None,
            "outcome_rate": round(outcomes / sent, 3) if sent else None,
            "sent": sent,
        },
    }
