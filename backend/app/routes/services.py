"""Services routes (spec v2 §5.2, §8.2). The work is in app/ai/offer_designer.py and app/service_catalog.py."""

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from .. import jobs, service_catalog
from ..ai import offer_designer
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()


class ServicesIn(BaseModel):
    services: list[dict] = Field(min_length=1, max_length=offer_designer.MAX_SERVICES)


class ApproveIn(BaseModel):
    version: int = Field(ge=1)


@router.post("/api/services/build", status_code=202, dependencies=[Depends(require_real), Depends(limit_actions)])
def build(background: BackgroundTasks, store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    if jobs.begin(store, offer_designer.JOB, step="demand", message="Reading hiring demand for your skills"):
        background.add_task(jobs.run, store, offer_designer.JOB, offer_designer.run_design, store, settings)
    return jobs.get(store, offer_designer.JOB)


@router.get("/api/services/status", dependencies=[Depends(require_real)])
def status(store=Depends(get_store)) -> dict:
    return jobs.get(store, offer_designer.JOB)


@router.get("/api/services", dependencies=[Depends(require_real)])
def get_services(store=Depends(get_store)) -> dict:
    return service_catalog.current(store)


@router.put("/api/services", dependencies=[Depends(require_real)])
def edit_services(body: ServicesIn, store=Depends(get_store)) -> dict:
    return service_catalog.edit(store, body.services)


@router.post("/api/services/approve", dependencies=[Depends(require_real)])
def approve(body: ApproveIn, store=Depends(get_store)) -> dict:
    return service_catalog.approve(store, body.version)
