"""Outreach drafts and the verifier.   OWNER: partner (to build)

Spec: docs/ai-agents.md → "Drafts & verifier". The route (app/routes/drafts.py) is
done: POST /api/leads/{id}/draft runs `run_draft` in the background; the UI polls
GET /api/leads/{id}/draft/status (result.draft_id when done).

Rules (CLAUDE.md): never invent experience; never below the rate floor; the writer
never grades itself (the model check is a separate call on the "check" model).
Save with store.add_draft({...}); shape: fixtures/drafts.json.
"""

from __future__ import annotations

from .. import jobs
from ..config import Settings
from . import client as ai  # noqa: F401  (used once drafting is built)


def job_key(lead_id: str) -> str:
    return f"draft:{lead_id}"


def write_draft(lead: dict, persona: dict, fit: dict, settings: Settings, feedback: list[str] | None = None) -> dict:
    """Return {subject, body, channel, to}. Under 150 words, one ask, quotes the ask rate."""
    raise NotImplementedError("write_draft (app/ai/drafts.py)")


def code_checks(draft: dict, lead: dict, persona: dict, fit: dict, floor: int) -> list[dict]:
    """[{name, passed, note}]: rate matches ask, not below floor, ≤150 words/one ask, channel allowed."""
    raise NotImplementedError("code_checks (app/ai/drafts.py)")


def model_check(draft: dict, lead: dict, persona: dict, settings: Settings) -> list[dict]:
    """Separate 'check' model call, fresh context: claims backed by the CV; professional tone."""
    raise NotImplementedError("model_check (app/ai/drafts.py)")


def run_draft(store, settings: Settings, lead_id: str) -> None:
    raise NotImplementedError("run_draft (app/ai/drafts.py)")
