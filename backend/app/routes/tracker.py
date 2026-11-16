"""Tracker routes (spec v2 §6.3). The work is in app/tracker.py; no logic here."""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from .. import jobs, tracker
from ..ai import drafts
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()

Status = Literal["sent", "positive_reply", "not_interested", "no_reply", "meeting", "won", "lost", "dropped", "ready"]


class StatusIn(BaseModel):
    status: Status
    note: str | None = Field(default=None, max_length=500)


class FollowupIn(BaseModel):
    language: Literal["nl", "en"] | None = None


@router.post("/api/leads/{lead_id}/status", dependencies=[Depends(require_real)])
def set_status(lead_id: str, body: StatusIn, store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    return tracker.set_status(store, lead_id, body.status, body.note, business_days=settings.reminder_business_days)


@router.get("/api/reminders", dependencies=[Depends(require_real)])
def reminders(all: bool = False, store=Depends(get_store)) -> dict:
    """Due "Did they reply?" reminders (all=true includes upcoming ones)."""
    return {"reminders": tracker.reminders(store, include_upcoming=all)}


@router.post("/api/leads/{lead_id}/followup", status_code=202,
             dependencies=[Depends(require_real), Depends(limit_actions)])
def followup(lead_id: str, background: BackgroundTasks, body: FollowupIn | None = None,
             store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    """Write the one follow-up (Writer -> Verifier). Poll GET /api/leads/{id}/draft/status."""
    lead = store.lead(lead_id)
    if not tracker.followup_allowed(store, lead):
        raise ValueError("A follow-up is only for a sent lead without a reply, and only once")
    key = drafts.job_key(lead_id)
    if jobs.begin(store, key, step="writing", message="Writing your follow-up"):
        background.add_task(jobs.run, store, key, drafts.run_draft, store, settings, lead_id,
                            body.language if body else None, "followup")
    return jobs.get(store, key)


@router.get("/api/funnel", dependencies=[Depends(require_real)])
def funnel(store=Depends(get_store)) -> dict:
    return tracker.funnel(store)
