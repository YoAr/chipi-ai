"""Lead search routes. Done; the work itself is app/ai/scoring.py."""

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from .. import jobs
from ..ai import scoring
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()


class SearchIn(BaseModel):
    keywords: list[str] | None = Field(default=None, max_length=10)


@router.post("/api/search", status_code=202, dependencies=[Depends(require_real), Depends(limit_actions)])
def search(background: BackgroundTasks, body: SearchIn | None = None,
           store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    keywords = body.keywords if body else None
    if jobs.begin(store, scoring.JOB, step="fetching", message="Searching job boards"):
        background.add_task(jobs.run, store, scoring.JOB, scoring.run_search, store, settings, keywords)
    return jobs.get(store, scoring.JOB)


@router.get("/api/search/status", dependencies=[Depends(require_real)])
def status(store=Depends(get_store)) -> dict:
    return jobs.get(store, scoring.JOB)
