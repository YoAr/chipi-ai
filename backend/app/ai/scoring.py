"""Lead search and scoring.   OWNER: partner (to build the scoring)

Spec: docs/ai-agents.md → "Search & scoring". The route (app/routes/search.py) is
done: POST /api/search runs `run_search` in the background; the UI polls
GET /api/search/status.

Already built and tested, ready to use:
- Fetchers: app.sources.remoteok / remotive / hn → fetch(httpx.Client, keywords, limit) -> list[Lead]
- Dedup: store.upsert_leads(leads) skips leads already stored (by dedup_key)
- Shapes: fixtures/leads.json
To build: `score_leads` (model + rules) and the WEIGHTS below.
"""

from __future__ import annotations

from .. import jobs
from ..config import Settings
from . import client as ai  # noqa: F401  (used once scoring is built)

JOB = "search"

# TUNE: how much each signal counts in the rules half of the score (product decision).
WEIGHTS = {
    "skills_overlap": 0.0,
    "rate_fit": 0.0,
    "remote": 0.0,
    "avoid_penalty": 0.0,
    "below_floor_penalty": 0.0,
}


def keywords_from_persona(persona: dict) -> list[str]:
    """Up to 6 search keywords from the persona's skills and wants."""
    skills = [s for s in persona.get("skills") or [] if s][:6]
    return skills or ["python"]


def score_leads(leads: list[dict], persona: dict, fit: dict, settings: Settings) -> list[dict]:
    """Return the leads with `score` (0–100) and a one-line `why` grounded in the post."""
    raise NotImplementedError("score_leads (app/ai/scoring.py)")


def run_search(store, settings: Settings, keywords: list[str] | None = None) -> None:
    import httpx

    from ..sources import hn, remoteok, remotive

    persona = store.get("persona")
    words = keywords or keywords_from_persona(persona)
    jobs.step(store, JOB, "fetching", "Searching RemoteOK, Remotive and Hacker News")
    found: list[dict] = []
    with httpx.Client(timeout=15, headers={"User-Agent": "chipi-ai/0.1"}) as client:
        for source in (remoteok, remotive, hn):
            found += source.fetch(client, words, limit=30)  # each returns [] on errors
    jobs.step(store, JOB, "scoring", f"Scoring {len(found)} posts")
    scored = score_leads(found[:25], persona, store.get("fit"), settings)
    added = store.upsert_leads(scored)
    jobs.done(store, JOB, f"Found {len(found)} posts, added {len(added)} new leads",
              {"found": len(found), "added": len(added)})
