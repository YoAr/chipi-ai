"""Researcher routes (spec v2 §5.3). The work is in app/ai/researcher.py; no logic here."""

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from .. import jobs
from ..ai import researcher
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()


class ResearchIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=25)
    lead_ids: list[str] | None = Field(default=None, max_length=25)


@router.post("/api/research", status_code=202, dependencies=[Depends(require_real), Depends(limit_actions)])
def start(background: BackgroundTasks, body: ResearchIn | None = None,
          store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    body = body or ResearchIn()
    if jobs.begin(store, researcher.JOB, step="researching", message="Researching leads"):
        background.add_task(jobs.run, store, researcher.JOB, researcher.run_research, store, settings,
                            body.limit, body.lead_ids)
    return jobs.get(store, researcher.JOB)


@router.get("/api/research/status", dependencies=[Depends(require_real)])
def status(store=Depends(get_store)) -> dict:
    return jobs.get(store, researcher.JOB)


@router.get("/api/leads/{lead_id}", dependencies=[Depends(require_real)])
def lead(lead_id: str, store=Depends(get_store)) -> dict:
    return store.lead(lead_id)
