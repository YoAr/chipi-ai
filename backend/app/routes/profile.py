"""Profile pipeline routes. Done; the work itself is app/ai/profile.py."""

from fastapi import APIRouter, BackgroundTasks, Depends

from .. import jobs
from ..ai import profile
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()


@router.post("/api/profile/build", status_code=202, dependencies=[Depends(require_real), Depends(limit_actions)])
def build(background: BackgroundTasks, store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    if jobs.begin(store, profile.JOB, step="persona", message="Reading your CV"):
        background.add_task(jobs.run, store, profile.JOB, profile.run_profile, store, settings)
    return jobs.get(store, profile.JOB)


@router.get("/api/profile/status", dependencies=[Depends(require_real)])
def status(store=Depends(get_store)) -> dict:
    return jobs.get(store, profile.JOB)
