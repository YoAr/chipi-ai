"""Draft routes. The work is in app/ai/drafts.py (Writer + Verifier); no logic here.

Nothing is sent from here: the freelancer copies a draft and sends it themselves (spec v2 §2.1).
"""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from .. import jobs
from ..ai import drafts
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()


class DraftIn(BaseModel):
    language: Literal["nl", "en"] | None = None


class CopiedIn(BaseModel):
    final_text: str = Field(min_length=1, max_length=5000)


class BatchIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=25)


@router.post("/api/leads/{lead_id}/draft", status_code=202,
             dependencies=[Depends(require_real), Depends(limit_actions)])
def draft(lead_id: str, background: BackgroundTasks, body: DraftIn | None = None,
          store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    """Write and verify a draft for a researched lead; `language` regenerates it in nl or en."""
    key = drafts.job_key(lead_id)
    if jobs.begin(store, key, step="writing", message="Writing your message"):
        background.add_task(jobs.run, store, key, drafts.run_draft, store, settings, lead_id,
                            body.language if body else None)
    return jobs.get(store, key)


@router.get("/api/leads/{lead_id}/draft/status", dependencies=[Depends(require_real)])
def status(lead_id: str, store=Depends(get_store)) -> dict:
    return jobs.get(store, drafts.job_key(lead_id))


@router.post("/api/drafts/write", status_code=202, dependencies=[Depends(require_real), Depends(limit_actions)])
def write_batch(background: BackgroundTasks, body: BatchIn | None = None,
                store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    if jobs.begin(store, "drafts", step="writing", message="Drafting researched leads"):
        background.add_task(jobs.run, store, "drafts", drafts.run_batch, store, settings, (body or BatchIn()).limit)
    return jobs.get(store, "drafts")


@router.get("/api/drafts/write/status", dependencies=[Depends(require_real)])
def batch_status(store=Depends(get_store)) -> dict:
    return jobs.get(store, "drafts")


@router.post("/api/drafts/{draft_id}/copied", dependencies=[Depends(require_real)])
def copied(draft_id: str, body: CopiedIn, store=Depends(get_store)) -> dict:
    """The freelancer copied the (possibly edited) text to send it themselves."""
    return store.copy_draft(draft_id, body.final_text)
