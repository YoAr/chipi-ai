"""Draft routes. Done; the work itself is app/ai/drafts.py."""

from fastapi import APIRouter, BackgroundTasks, Depends

from .. import jobs
from ..ai import drafts
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()


@router.post("/api/leads/{lead_id}/draft", status_code=202,
             dependencies=[Depends(require_real), Depends(limit_actions)])
def draft(lead_id: str, background: BackgroundTasks, store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    key = drafts.job_key(lead_id)
    if jobs.begin(store, key, step="writing", message="Writing your message"):
        background.add_task(jobs.run, store, key, drafts.run_draft, store, settings, lead_id)
    return jobs.get(store, key)


@router.get("/api/leads/{lead_id}/draft/status", dependencies=[Depends(require_real)])
def status(lead_id: str, store=Depends(get_store)) -> dict:
    return jobs.get(store, drafts.job_key(lead_id))
