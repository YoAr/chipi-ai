"""In-memory store seeded from fixtures/. Used when MOCK_MODE=1.

Writes change the in-memory copy so the UI can click through a realistic flow;
POST /api/reset (or a restart) restores the fixtures. The real store will offer
the same methods, backed by SQLite, the wiki and events.jsonl.
"""

import copy
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from .guards import check_send_allowed

FIXTURE_NAMES = ("state", "persona", "fit", "offers", "leads", "drafts", "pipeline", "metrics")
PERSONA_FIELDS = {
    "name", "role", "years", "location", "skills", "wants", "avoids",
    "hours_per_week", "income_goal_monthly",
}
# How far along a lead is; matches the pipeline columns (Drafted, Sent, Replied, Active, Done).
LEAD_STAGE = {"drafted": 0, "sent": 1, "replied": 2, "won": 3, "lost": 4}


class NotFound(Exception):
    """Raised when an id does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MockStore:
    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures_dir = fixtures_dir
        self.reset()

    def reset(self) -> None:
        self._data = {
            name: json.loads((self.fixtures_dir / f"{name}.json").read_text())
            for name in FIXTURE_NAMES
        }
        self._ids = itertools.count(100)

    def get(self, name: str) -> dict:
        return copy.deepcopy(self._data[name])

    # Persona ---------------------------------------------------------------
    def update_persona(self, fields: dict) -> dict:
        persona = self._data["persona"]
        persona.update({k: v for k, v in fields.items() if k in PERSONA_FIELDS})
        return copy.deepcopy(persona)

    def add_cv(self, filename: str) -> dict:
        """Mock mode records the file name only; the real store saves it to raw/ and runs the Interviewer."""
        persona = self._data["persona"]
        source = f"raw/{datetime.now(timezone.utc):%Y-%m-%d}-{Path(filename).name}"
        if source not in persona["sources"]:
            persona["sources"].append(source)
        return copy.deepcopy(persona)

    # Leads -----------------------------------------------------------------
    def leads(self, status: str | None = None) -> dict:
        leads = self._data["leads"]["leads"]
        if status:
            leads = [lead for lead in leads if lead["status"] == status]
        return {"_sample": True, "leads": copy.deepcopy(leads)}

    def _lead(self, lead_id: str) -> dict:
        for lead in self._data["leads"]["leads"]:
            if lead["id"] == lead_id:
                return lead
        raise NotFound(f"No lead with id {lead_id}")

    def set_verdict(self, lead_id: str, verdict: str, note: str | None) -> dict:
        lead = self._lead(lead_id)
        lead["status"] = verdict
        if note:
            lead["verdict_note"] = note
        return copy.deepcopy(lead)

    def add_seed(self, url: str) -> dict:
        lead = {
            "id": f"ld_{next(self._ids)}",
            "nickname": "pasted-link",
            "title": "Pasted link (not fetched yet)",
            "company": None,
            "source": "seed",
            "url": url,
            "fetched": False,
            "rate_text": None,
            "score": None,
            "why": "Waiting to be fetched and scored",
            "status": "new",
            "found_at": _now(),
        }
        self._data["leads"]["leads"].insert(0, lead)
        return copy.deepcopy(lead)

    # Drafts ----------------------------------------------------------------
    def drafts(self, status: str | None = None) -> dict:
        drafts = self._data["drafts"]["drafts"]
        if status:
            drafts = [d for d in drafts if d["status"] == status]
        return {"_sample": True, "drafts": copy.deepcopy(drafts)}

    def _draft(self, draft_id: str) -> dict:
        for draft in self._data["drafts"]["drafts"]:
            if draft["id"] == draft_id:
                return draft
        raise NotFound(f"No draft with id {draft_id}")

    def approve_draft(self, draft_id: str, final_text: str) -> dict:
        """Approve only, as in real mode: the user then copies it, sends it and marks it sent."""
        draft = self._draft(draft_id)
        if draft["status"] != "pending":
            raise ValueError(f"Draft {draft_id} is {draft['status']}; only a pending draft can be approved")
        draft["body"] = final_text
        draft["status"] = "approved"
        draft["approved_at"] = _now()
        return copy.deepcopy(draft)

    def reject_draft(self, draft_id: str, note: str) -> dict:
        draft = self._draft(draft_id)
        draft["status"] = "rejected"
        draft["reject_note"] = note
        return copy.deepcopy(draft)

    # After approval: sent, replied, won (same rules as RealStore; nothing is logged) --------
    def _find_on_board(self, nickname: str) -> tuple[int | None, dict | None]:
        for i, col in enumerate(self._data["pipeline"]["columns"]):
            for item in col["items"]:
                if item["nickname"] == nickname:
                    return i, item
        return None, None

    def _advance(self, nickname: str, title: str, column: str, note: str) -> None:
        """Move a lead's card forward to `column` (never backwards), or add it there."""
        cols = self._data["pipeline"]["columns"]
        target = next(i for i, c in enumerate(cols) if c["name"] == column)
        at, item = self._find_on_board(nickname)
        if at is not None and at >= target:
            return
        if item is not None:
            cols[at]["items"].remove(item)
        cols[target]["items"].insert(0, {"nickname": nickname, "title": title or nickname, "note": note})

    def _stage(self, lead: dict) -> int:
        at, _ = self._find_on_board(lead["nickname"])
        return max(LEAD_STAGE.get(lead["status"], 0), at if at is not None else 0)

    def mark_sent(self, draft_id: str) -> dict:
        draft = self._draft(draft_id)
        if draft["status"] == "sent":
            return copy.deepcopy(draft)
        check_send_allowed(draft)  # product rule 1: nothing goes out unapproved
        draft["status"] = "sent"
        draft["sent_at"] = _now()
        try:
            lead = self._lead(draft["lead_id"])
        except NotFound:
            lead = None
        if lead is not None:
            if LEAD_STAGE.get(lead["status"], 0) < LEAD_STAGE["sent"]:
                lead["status"] = "sent"
            self._advance(lead["nickname"], lead["title"], "Sent", "No reply yet")
        return copy.deepcopy(draft)

    def _require_sent(self, lead: dict) -> None:
        if self._stage(lead) < LEAD_STAGE["sent"]:
            raise ValueError("Nothing has been sent to this lead yet; mark the message sent first")

    def mark_replied(self, lead_id: str, note: str | None = None) -> dict:
        lead = self._lead(lead_id)
        if self._stage(lead) >= LEAD_STAGE["replied"]:
            return copy.deepcopy(lead)
        self._require_sent(lead)
        lead["status"] = "replied"
        self._advance(lead["nickname"], lead["title"], "Replied", "Reply received")
        return copy.deepcopy(lead)

    def mark_won(self, lead_id: str, amount: float | None = None, note: str | None = None) -> dict:
        if amount is not None and amount < 0:
            raise ValueError("amount can't be negative")
        lead = self._lead(lead_id)
        if self._stage(lead) >= LEAD_STAGE["won"]:
            return copy.deepcopy(lead)
        self._require_sent(lead)
        lead["status"] = "won"
        self._advance(lead["nickname"], lead["title"], "Active", lead.get("rate_text") or "Won")
        return copy.deepcopy(lead)

