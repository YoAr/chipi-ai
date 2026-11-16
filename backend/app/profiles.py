"""The freelancer's profile: versions, the human approval gate, and the rate floor.

The approved profile owns the rate floor (CLAUDE.md rule 2): `rate_floor(store)` is what every
price check reads. Approving a profile also hands its exclusions to Scout and keeps the older
persona doc in step, so screens that still read /api/persona show the approved facts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .ai.interviewer import Profile
from .storage import versions
from .storage.events import append_event

NAME = "profile"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current(store) -> dict:
    """{"latest": newest version or None, "approved": approved version or None}."""
    return {"latest": versions.latest(store.conn, NAME), "approved": versions.approved(store.conn, NAME)}


def approved_profile(store) -> dict | None:
    row = versions.approved(store.conn, NAME)
    return row["data"] if row else None


def rate_floor(store) -> float | None:
    """EUR/hour floor from the approved profile; None until one is approved (then nothing is priced)."""
    profile = approved_profile(store)
    floor = (profile or {}).get("rate_floor_eur_hour")
    return float(floor) if floor is not None else None


def edit(store, data: dict) -> dict:
    """Save the freelancer's own edits as a new draft. Their words are a source, so no grounding."""
    profile = Profile.model_validate(data)
    checks = [{"name": "Rate floor given", "passed": profile.rate_floor_eur_hour is not None,
               "note": None if profile.rate_floor_eur_hour is not None else "Add your minimum hourly rate before approving"}]
    saved = versions.save(store.conn, NAME, profile.model_dump(), author="user",
                          provenance={"model": None, "prompt_version": None, "input_ref": "user edit", "ts": _now()},
                          checks=checks)
    append_event(store.data_dir, "persona_updated", "user", "persona",
                 {"profile_version": saved["version"], "edited": True})
    return saved


def approve(store, version: int) -> dict:
    row = versions.get(store.conn, NAME, version)
    if row is None:
        from .mock_store import NotFound
        raise NotFound(f"No profile version {version}")
    profile = Profile.model_validate(row["data"])
    if profile.rate_floor_eur_hour is None or profile.rate_floor_eur_hour <= 0:
        raise ValueError("Add your minimum hourly rate (EUR) before approving")
    approved = versions.approve(store.conn, NAME, version)

    # Hand-offs: Scout's exclusions, and the older persona doc the current screens read
    store.set_setting("profile_exclusions", profile.exclusions)
    persona_fields = {
        "skills": [s.name for s in profile.skills][:40],
        "avoids": "; ".join(profile.exclusions)[:500],
        "wants": "; ".join(profile.preferred_work)[:500],
    }
    if profile.availability_hours_week and 1 <= profile.availability_hours_week <= 80:
        persona_fields["hours_per_week"] = int(profile.availability_hours_week)
    store.update_persona(persona_fields)
    append_event(store.data_dir, "profile_approved", "user", "persona",
                 {"version": version, "rate_floor_eur_hour": profile.rate_floor_eur_hour})
    return approved
