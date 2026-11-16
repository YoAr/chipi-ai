"""Interviewer and profile routes (spec v2 §5.1, §8.1-2). The work is in app/ai/interviewer.py
and app/profiles.py; no logic here.

Interview turns are short single model calls, so they answer directly. Building the profile from
the whole transcript is longer and runs as a background job (202 + poll GET /api/interview).
"""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from .. import jobs, profiles
from ..ai import interviewer
from ..ai.interviewer import Profile
from ..deps import get_settings, get_store, limit_actions, require_real

router = APIRouter()


class StartIn(BaseModel):
    language: Literal["nl", "en"] = "en"
    segment: str | None = Field(default=None, max_length=120)


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


class ApproveIn(BaseModel):
    version: int = Field(ge=1)


@router.get("/api/interview", dependencies=[Depends(require_real)])
def interview_state(store=Depends(get_store)) -> dict:
    return interviewer.public_state(store)


@router.post("/api/interview/start", dependencies=[Depends(require_real), Depends(limit_actions)])
def start(body: StartIn, store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    return interviewer.start(store, settings, body.language, body.segment)


@router.post("/api/interview/answer", dependencies=[Depends(require_real), Depends(limit_actions)])
def answer(body: AnswerIn, background: BackgroundTasks, store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    state = interviewer.answer(store, settings, body.answer)
    if state["status"] == "complete":
        _start_extract(background, store, settings)
    return interviewer.public_state(store)


@router.post("/api/interview/finish", status_code=202, dependencies=[Depends(require_real), Depends(limit_actions)])
def finish(background: BackgroundTasks, store=Depends(get_store), settings=Depends(get_settings)) -> dict:
    """End the interview now (or rebuild the profile from a finished one)."""
    state = interviewer.get_state(store)
    if not state or state["status"] == "active":
        interviewer.finish(store)
    _start_extract(background, store, settings)
    return interviewer.public_state(store)


def _start_extract(background: BackgroundTasks, store, settings) -> None:
    if jobs.begin(store, interviewer.JOB, step="transcript", message="Building your profile"):
        background.add_task(jobs.run, store, interviewer.JOB, interviewer.run_extract, store, settings)


@router.get("/api/profile", dependencies=[Depends(require_real)])
def get_profile(store=Depends(get_store)) -> dict:
    return profiles.current(store)


@router.put("/api/profile", dependencies=[Depends(require_real)])
def edit_profile(body: Profile, store=Depends(get_store)) -> dict:
    return profiles.edit(store, body.model_dump())


@router.post("/api/profile/approve", dependencies=[Depends(require_real)])
def approve_profile(body: ApproveIn, store=Depends(get_store)) -> dict:
    return profiles.approve(store, body.version)
