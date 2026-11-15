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

FIXTURE_NAMES = ("state", "persona", "fit", "offers", "leads", "drafts", "pipeline", "metrics")
PERSONA_FIELDS = {
    "name", "role", "years", "location", "skills", "wants", "avoids",
    "hours_per_week", "income_goal_monthly",
}


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
        draft = self._draft(draft_id)
        draft["body"] = final_text
        draft["status"] = "sent"  # mock mode: approving sends immediately, nothing leaves the server
        draft["approved_at"] = draft["sent_at"] = _now()
        return copy.deepcopy(draft)

    def reject_draft(self, draft_id: str, note: str) -> dict:
        draft = self._draft(draft_id)
        draft["status"] = "rejected"
        draft["reject_note"] = note
        return copy.deepcopy(draft)
