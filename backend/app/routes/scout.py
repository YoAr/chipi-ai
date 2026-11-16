"""Scout routes (spec v2 §6.1). The work itself is app/scout.py; no model calls."""

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from .. import jobs, scout
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()


class ScoutIn(BaseModel):
    terms: list[str] | None = Field(default=None, max_length=scout.MAX_TERMS)


@router.post("/api/scout", status_code=202, dependencies=[Depends(require_real), Depends(limit_actions)])
def start_scout(background: BackgroundTasks, body: ScoutIn | None = None,
                store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    terms = body.terms if body else None
    if jobs.begin(store, scout.JOB, step="discovering", message="Searching Recruitee boards"):
        background.add_task(jobs.run, store, scout.JOB, scout.run_scout, store, settings, terms)
    return jobs.get(store, scout.JOB)


@router.get("/api/scout/status", dependencies=[Depends(require_real)])
def scout_status(store=Depends(get_store)) -> dict:
    return jobs.get(store, scout.JOB)


@router.post("/api/panel/snapshot", status_code=202, dependencies=[Depends(require_real), Depends(limit_actions)])
def start_snapshot(background: BackgroundTasks, store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    if jobs.begin(store, scout.SNAPSHOT_JOB, step="discovering", message="Snapshotting the baseline panel"):
        background.add_task(jobs.run, store, scout.SNAPSHOT_JOB, scout.run_snapshot, store, settings)
    return jobs.get(store, scout.SNAPSHOT_JOB)


@router.get("/api/panel/snapshot/status", dependencies=[Depends(require_real)])
def snapshot_status(store=Depends(get_store)) -> dict:
    return jobs.get(store, scout.SNAPSHOT_JOB)
