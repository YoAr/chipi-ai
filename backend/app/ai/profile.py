"""Profile pipeline: CV → persona → fit & rate → offers.   OWNER: partner (to build)

Spec: docs/ai-agents.md → "Profile". The route (app/routes/profile.py) is done:
POST /api/profile/build runs `run_profile` in the background and the UI polls
GET /api/profile/status. Fill in the three steps; keep the signatures.

Building blocks:
- Model calls: `ai.structured("smart", settings, instructions=..., input=..., schema=YourModel)`
  (tests monkeypatch `app.ai.profile.ai.structured`, so always call it through `ai.`)
- Progress: `jobs.step(store, "profile", step, message)`; finish with `jobs.done(...)`
- CV text: settings.data_dir / store.get_setting("cv_text_path")
- Save: store.update_persona(fields) · store.set_doc("fit", {...}) · store.set_doc("offers", {...})
- Shapes: fixtures/persona.json, fixtures/fit.json, fixtures/offers.json
- Rate floor: app.profiles.rate_floor(store) (EUR/hour, from the approved profile)
"""

from __future__ import annotations

from .. import jobs
from ..config import Settings
from . import client as ai  # noqa: F401  (used once the steps are built)

JOB = "profile"


def build_persona(store, settings: Settings) -> dict:
    """Read the uploaded CV text and save the persona. Extract only what the CV says."""
    raise NotImplementedError("build_persona (app/ai/profile.py)")


def build_fit(store, settings: Settings) -> dict:
    """Fit and rate with cited reasons; goal checks run in code, not by the model."""
    raise NotImplementedError("build_fit (app/ai/profile.py)")


def build_offers(store, settings: Settings) -> dict:
    """Three packaged offers from the persona and fit."""
    raise NotImplementedError("build_offers (app/ai/profile.py)")


def run_profile(store, settings: Settings) -> None:
    jobs.step(store, JOB, "persona", "Reading your CV")
    build_persona(store, settings)
    jobs.step(store, JOB, "fit", "Working out your rate")
    build_fit(store, settings)
    jobs.step(store, JOB, "offers", "Shaping your offers")
    build_offers(store, settings)
    jobs.done(store, JOB, "Your profile, rate and offers are ready")
