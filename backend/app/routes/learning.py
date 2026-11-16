"""Learner jobs, proposal approvals and deterministic outreach insights."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from pydantic import BaseModel, StringConstraints

from .. import insights, jobs, learning
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter(dependencies=[Depends(require_real)])


class MeetingIn(BaseModel):
    lead_id: str
    held_on: date
    transcript: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)]
    consent_confirmed: Literal[True]


class ApprovalIn(BaseModel):
    kind: Literal["skill", "service"]
    index: int


class EngagementIn(BaseModel):
    lead_id: str


@router.post("/api/meetings", status_code=202, dependencies=[Depends(limit_actions)])
def meeting(body: MeetingIn, background: BackgroundTasks, store=Depends(get_store), settings=Depends(get_settings)):
    meeting_id = learning.start_meeting(store, body.lead_id, body.held_on.isoformat(), body.transcript)
    if meeting_id is None:
        raise ValueError("A meeting or engagement review is already running")
    background.add_task(jobs.run, store, learning.JOB, learning.run, store, settings, body.lead_id, meeting_id)
    return jobs.get(store, learning.JOB)


@router.post("/api/learnings/engagement", status_code=202, dependencies=[Depends(limit_actions)])
def engagement(body: EngagementIn, background: BackgroundTasks, store=Depends(get_store), settings=Depends(get_settings)):
    if not learning.start_summary(store, body.lead_id):
        raise ValueError("A meeting or engagement review is already running")
    background.add_task(jobs.run, store, learning.JOB, learning.run, store, settings, body.lead_id)
    return jobs.get(store, learning.JOB)


@router.get("/api/learnings/status")
def status(response: Response, store=Depends(get_store)):
    response.headers["Cache-Control"] = "no-store"
    return jobs.get(store, learning.JOB)


@router.get("/api/learnings")
def learnings(response: Response, store=Depends(get_store)):
    response.headers["Cache-Control"] = "no-store"
    return {"learnings": learning.current(store), "engagements": learning.engagements(store)}


@router.post("/api/learnings/{learning_id}/approve")
def approve(learning_id: str, body: ApprovalIn, store=Depends(get_store)):
    return learning.approve(store, learning_id, body.kind, body.index)


@router.get("/api/insights")
def get_insights(store=Depends(get_store)):
    return insights.current(store)
