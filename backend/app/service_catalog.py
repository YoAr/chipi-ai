"""The freelancer's services: versions, edits and the approval gate (spec v2 §2.1, §5.2).

Approving services hands their Dutch and English discovery terms to Scout (its default search
terms) and appends a `services_approved` event. The freelancer's own edits are saved as a new
draft: the rate floor is enforced (rule 2); Wet DBA phrases are flagged as guidance, not blocked,
because the freelancer owns that decision (spec §2.5).
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import demand, profiles
from .ai.offer_designer import MAX_WEEKS, NAME, STRENGTH_ORDER, Service
from .compliance import find_banned
from .storage import versions
from .storage.events import append_event


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current(store) -> dict:
    return {"latest": versions.latest(store.conn, NAME), "approved": versions.approved(store.conn, NAME)}


def approved_services(store) -> list[dict]:
    row = versions.approved(store.conn, NAME)
    return row["data"]["services"] if row else []


def edit(store, services: list[dict]) -> dict:
    """Save the freelancer's edits as a new draft; demand and proof are recomputed in code."""
    profile = profiles.approved_profile(store)
    if not profile:
        raise ValueError("Approve your profile first")
    floor = profiles.rate_floor(store)
    known = {s["name"].strip().lower(): s for s in profile["skills"]}
    out, warnings = [], []
    for raw in services:
        s = Service.model_validate(raw)
        if not 1 <= s.duration_weeks <= MAX_WEEKS:
            raise ValueError(f"{s.name}: enter a duration between 1 and {MAX_WEEKS} weeks")
        if not s.rate_range_eur.min <= s.rate_range_eur.max < float("inf"):
            raise ValueError(f"{s.name}: maximum rate must be finite and at least the minimum")
        if s.rate_range_eur.min < floor:
            raise ValueError(f"{s.name}: €{s.rate_range_eur.min:g} is below your floor of €{floor:g}/hour")
        skills = [known[k.strip().lower()]["name"] for k in s.skills_used if k.strip().lower() in known]
        if not skills:
            raise ValueError(f"{s.name}: use at least one skill from your approved profile")
        strength = max((known[k.strip().lower()]["proof_strength"] for k in skills), key=lambda x: STRENGTH_ORDER[x])
        banned = find_banned(" ".join([s.name, s.target_buyer, s.problem, s.deliverable]))
        if banned:
            warnings.append(f"{s.name}: {', '.join(banned)}")
        idx = demand.service_index(store.conn, skills, strength)
        out.append({**s.model_dump(), "skills_used": skills, "proof_strength": strength,
                    "demand_index": idx["value"] or 0.0, "demand": idx})
    previous = versions.latest(store.conn, NAME)
    checks = [{"name": "Independent project framing (Wet DBA)", "passed": not warnings,
               "note": ("Consider rewording: " + " | ".join(warnings)) if warnings else
               "Guidance only, not legal advice."}]
    return versions.save(store.conn, NAME, {"services": out, "market": (previous or {}).get("data", {}).get("market")},
                         author="user", provenance={"model": None, "prompt_version": None,
                                                    "input_ref": "user edit", "ts": _now()}, checks=checks)


def approve(store, version: int) -> dict:
    row = versions.get(store.conn, NAME, version)
    if row is None:
        from .mock_store import NotFound
        raise NotFound(f"No services version {version}")
    services = row["data"]["services"]
    floor = profiles.rate_floor(store)
    if floor is None:
        raise ValueError("Approve your profile first")
    low = [s["name"] for s in services if s["rate_range_eur"]["min"] < floor]
    if low:
        raise ValueError(f"Below your floor of €{floor:g}/hour: {', '.join(low)}")
    approved = versions.approve(store.conn, NAME, version)
    terms = {lang: list(dict.fromkeys(t for s in services for t in s["discovery_terms"].get(lang, [])))
             for lang in ("nl", "en")}
    store.set_setting("discovery_terms", terms)
    append_event(store.data_dir, "services_approved", "user", "persona",
                 {"version": version, "services": [s["id"] for s in services]})
    return approved
