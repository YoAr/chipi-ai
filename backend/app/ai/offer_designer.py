"""Offer designer (spec v2 §5.2) · Judgment tier ("smart").

Approved profile + demand inputs (code) + EUR rate benchmarks -> 3-5 sellable services framed as
independent projects, with NL and EN discovery terms that drive Scout.

The model proposes; code decides what is safe to show (CLAUDE.md: rules hold in code):
- skills_used come from the approved profile; at least one is demonstrated or referenceable;
  services combine skills (2+), not single skills
- bounded deliverable (1-26 weeks) with an independence rationale; no Wet DBA risk phrases
  (app/compliance.py) and nothing the freelancer excluded
- rate range never below the approved floor (rule 2)
- 2-6 discovery terms each in Dutch and English
- demand_index and proof_strength are set by code (app/demand.py), never by the model
One retry with the failures as feedback; services still failing a hard check are dropped and
listed. Fewer than 3 left -> the job fails with the reasons.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .. import demand, jobs, profiles
from ..compliance import find_banned
from ..config import Settings
from ..rates import load_benchmarks
from ..storage import versions
from . import client as ai

JOB = "services"
NAME = "services"
PROMPT_VERSION = "offer-designer-v1"
MIN_SERVICES, MAX_SERVICES = 3, 5
MAX_WEEKS = 26
STRENGTH_ORDER = {"claimed": 0, "demonstrated": 1, "referenceable": 2}


class RateRange(BaseModel):
    min: float
    max: float


class DiscoveryTerms(BaseModel):
    nl: list[str]
    en: list[str]


class Service(BaseModel):
    id: str
    name: str
    target_buyer: str
    problem: str
    deliverable: str
    duration_weeks: float
    skills_used: list[str]
    rate_range_eur: RateRange
    demand_index: float = Field(description="Set to 0; code fills it in.")
    proof_strength: Literal["claimed", "demonstrated", "referenceable"]
    independence_rationale: str
    rationale: str = Field(description="Why this sells, referring to the demand inputs you were given.")
    discovery_terms: DiscoveryTerms


class Services(BaseModel):
    services: list[Service]


INSTRUCTIONS = """You design 3 to 5 sellable services for an independent freelancer in Amsterdam / the Netherlands, from their APPROVED PROFILE, the HIRING DEMAND INPUTS (computed in code from Dutch job postings) and EUR RATE BENCHMARKS.

Rules:
- Build each service around a COMBINATION of 2+ skills from the profile (use the exact skill names). Each service must use at least one skill with proof_strength "demonstrated" or "referenceable".
- Each service: a specific target buyer, the problem they have, a CONCRETE DELIVERABLE WITH AN END POINT, and duration_weeks (1-26).
- Wet DBA (Dutch disguised-employment rules): every service is independent, project-based work with a defined result. Fixed scope, the freelancer's own methods and tools, several clients possible. Never open-ended, never hours-based, never "joining the team" or working under the client's direction. Write independence_rationale: why this is independent work.
- Never offer work the freelancer excluded.
- rate_range_eur: EUR per hour equivalent, min at or above the freelancer's rate floor of {floor}, informed by the benchmarks.
- Prefer combinations with stronger hiring demand and stronger proof. Explain the demand inputs in rationale; do not invent numbers that are not in the inputs.
- discovery_terms: 2-6 short job-search terms each in Dutch (nl) and English (en) that companies needing this service would use in vacancy titles (e.g. "data engineer", "projectmanager ERP").
- demand_index: set 0 (code computes it). proof_strength: your best guess (code recomputes it). id: short kebab-case.
- Use only facts from the profile. Never invent clients, numbers or results."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "service"


def _profile_skills(profile: dict) -> dict[str, dict]:
    return {s["name"].strip().lower(): s for s in profile.get("skills", [])}


def service_problems(s: Service, profile: dict, floor: float) -> tuple[list[str], list[str]]:
    """(hard problems -> drop the service, soft problems -> fixable notes)."""
    hard, soft = [], []
    known = _profile_skills(profile)
    unknown = [k for k in s.skills_used if k.strip().lower() not in known]
    if unknown:
        hard.append(f"skills not in the approved profile: {', '.join(unknown)}")
    if len(s.skills_used) < 2:
        hard.append("built on a single skill, not a combination")
    proven = [k for k in s.skills_used if STRENGTH_ORDER.get(known.get(k.strip().lower(), {}).get("proof_strength"), 0) >= 1]
    if not proven:
        hard.append("no demonstrated or referenceable skill")
    if not s.deliverable.strip() or not (1 <= s.duration_weeks <= MAX_WEEKS):
        hard.append(f"deliverable must be concrete and 1-{MAX_WEEKS} weeks")
    if not s.independence_rationale.strip():
        hard.append("missing independence rationale")
    text = " ".join([s.name, s.target_buyer, s.problem, s.deliverable, s.rationale, s.independence_rationale])
    banned = find_banned(text)
    if banned:
        hard.append("Wet DBA risk phrases: " + ", ".join(banned))
    low = text.lower()
    excluded = [e for e in profile.get("exclusions", []) if len(e) >= 4 and e.lower() in low]
    if excluded:
        hard.append(f"offers excluded work: {', '.join(excluded)}")
    if s.rate_range_eur.min < floor:
        soft.append(f"rate min €{s.rate_range_eur.min:g} is below your floor €{floor:g}")
    if s.rate_range_eur.max < s.rate_range_eur.min:
        soft.append("rate max below min")
    for lang in ("nl", "en"):
        terms = [t for t in getattr(s.discovery_terms, lang) if t.strip()]
        if not 2 <= len(terms) <= 6:
            soft.append(f"needs 2-6 {lang} discovery terms")
    return hard, soft


def _feedback(problems: dict[str, list[str]], count: int) -> str:
    lines = [f"- {sid}: {'; '.join(p)}" for sid, p in problems.items() if p]
    if not MIN_SERVICES <= count <= MAX_SERVICES:
        lines.append(f"- return {MIN_SERVICES}-{MAX_SERVICES} services (you returned {count})")
    return "\n\nYOUR LAST ANSWER FAILED THESE CHECKS; FIX THEM:\n" + "\n".join(lines)


def _inputs(profile: dict, table: dict, bench: dict, floor: float) -> str:
    return json.dumps({"approved_profile": profile, "rate_floor_eur_hour": floor,
                       "hiring_demand_inputs": table,
                       "eur_rate_benchmarks": {"about": bench.get("_about"), "hourly": [
                           {k: e.get(k) for k in ("source", "typical", "low", "high", "n", "roles", "date_modified", "raw")}
                           for e in bench.get("hourly", [])]}},
                      ensure_ascii=False, default=str)


def _finalize(s: Service, profile: dict, floor: float, conn) -> tuple[dict, list[str]]:
    notes = []
    known = _profile_skills(profile)
    skills = [known[k.strip().lower()]["name"] for k in s.skills_used]
    strength = max((known[k.strip().lower()]["proof_strength"] for k in skills), key=lambda x: STRENGTH_ORDER[x])
    lo, hi = s.rate_range_eur.min, s.rate_range_eur.max
    if lo < floor:
        notes.append(f"{s.name}: rate min raised from €{lo:g} to your floor €{floor:g}")
        lo = floor
    hi = max(hi, lo)
    idx = demand.service_index(conn, skills, strength)
    out = s.model_dump()
    out.update({"skills_used": skills, "proof_strength": strength, "rate_range_eur": {"min": lo, "max": hi},
                "demand_index": idx["value"] if idx["value"] is not None else 0.0, "demand": idx,
                "discovery_terms": {lang: [t.strip() for t in getattr(s.discovery_terms, lang) if t.strip()][:6]
                                    for lang in ("nl", "en")}})
    return out, notes


def run_design(store, settings: Settings) -> dict:
    profile = profiles.approved_profile(store)
    if not profile:
        raise ValueError("Approve your profile first")
    floor = profiles.rate_floor(store)
    jobs.step(store, JOB, "demand", "Reading hiring demand for your skills")
    table = demand.market_table(store.conn, profile)
    bench = load_benchmarks(settings.data_dir)
    from ..learning import approved_service_changes

    base = _inputs(profile, table, bench, floor)
    changes = approved_service_changes(store)
    if changes:
        base += "\nUser-approved service suggestions to consider: " + json.dumps(changes, ensure_ascii=False)
    instructions = INSTRUCTIONS.format(floor=f"€{floor:g}/hour")

    jobs.step(store, JOB, "designing", "Designing your services")
    result = ai.structured("smart", settings, instructions=instructions, input=base, schema=Services)
    problems = {s.id or s.name: sum(service_problems(s, profile, floor), []) for s in result.services}
    if any(problems.values()) or not MIN_SERVICES <= len(result.services) <= MAX_SERVICES:
        jobs.step(store, JOB, "revising", "Fixing services that failed a check")
        result = ai.structured("smart", settings, instructions=instructions,
                               input=base + _feedback(problems, len(result.services)), schema=Services)

    kept, dropped, notes, seen = [], [], [], set()
    for s in result.services[:MAX_SERVICES]:
        hard, soft = service_problems(s, profile, floor)
        if hard:
            dropped.append(f"{s.name}: {'; '.join(hard)}")
            continue
        svc, fix_notes = _finalize(s, profile, floor, store.conn)
        sid = _slug(svc["id"] or svc["name"])
        while sid in seen:
            sid += "-2"
        seen.add(sid)
        svc["id"] = sid
        kept.append(svc)
        notes += fix_notes + [f"{s.name}: {p}" for p in soft if "below your floor" not in p]
    if len(kept) < MIN_SERVICES:
        raise ValueError(f"Couldn't design {MIN_SERVICES} services that pass the checks. "
                         + (" Dropped: " + " | ".join(dropped) if dropped else ""))

    kept.sort(key=lambda s: s["demand_index"], reverse=True)
    checks = [
        {"name": f"{MIN_SERVICES}-{MAX_SERVICES} services", "passed": True, "note": f"{len(kept)} services"},
        {"name": "Skills from your approved profile, with proof, in combinations", "passed": not dropped,
         "note": ("Dropped: " + " | ".join(dropped)) if dropped else None},
        {"name": "Independent project framing (Wet DBA)", "passed": True,
         "note": "Checked against the starter phrase list. Guidance only, not legal advice."},
        {"name": "Rates at or above your floor", "passed": not any("rate min raised" in n for n in notes),
         "note": "; ".join(n for n in notes if "rate min raised" in n) or None},
        {"name": "Dutch and English discovery terms", "passed": not any("discovery terms" in n for n in notes),
         "note": "; ".join(n for n in notes if "discovery terms" in n) or None},
        {"name": "Demand index computed in code", "passed": table["panel_postings"] > 0,
         "note": (f"{table['panel_postings']} panel postings over {table['snapshot_days']} snapshot day(s)"
                  if table["panel_postings"] else "No panel snapshot yet: demand shows as 0 until one runs")},
    ]
    provenance = {"model": ai.model_for("smart", settings), "prompt_version": PROMPT_VERSION,
                  "input_ref": {"profile_version": versions.approved(store.conn, "profile")["version"],
                                "benchmarks": [e.get("raw") for e in bench.get("hourly", [])],
                                "panel_last_day": table["last_day"]}, "ts": _now()}
    saved = versions.save(store.conn, NAME, {"services": kept, "market": table}, author="agent",
                          provenance=provenance, checks=checks)
    jobs.done(store, JOB, f"{len(kept)} services ready to review", {"version": saved["version"]})
    return saved
