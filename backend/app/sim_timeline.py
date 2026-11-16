"""The "Simulate 2 weeks" demo: a fixed, generated history and its metrics day by day.

Nothing here touches real data. The simulation is deterministic, so the whole
timeline is computed once (in the background at startup) and cached; the
Metrics screen then animates through real day-by-day snapshots, not interpolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache

from .metrics import compute_metrics
from .simulate import simulate

# Channel reply rates chosen so one lesson clears the Learner's confidence bar
# (tests/test_metrics.py checks it doesn't invent lessons from noise).
DEMO_TRUE_RATES = {"Direct email": 0.65, "HN Who's Hiring": 0.45, "Web search": 0.3, "RemoteOK": 0.1}
DEMO_SIM = {"days": 14, "seed": 3, "sends_per_day": 6}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _week(d: datetime) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


@lru_cache(maxsize=1)
def demo_timeline() -> dict:
    """Final metrics plus one snapshot per day (day 0 = before any outreach)."""
    events = simulate(true_rates=DEMO_TRUE_RATES, **DEMO_SIM)
    start = _parse(events[0]["ts"]).replace(hour=0, minute=0, second=0)
    points = []
    for day in range(DEMO_SIM["days"] + 1):
        # Day 0 = before any outreach; day d = end of the d-th simulated day
        cutoff = start + timedelta(days=day - 1, hours=23, minutes=59) if day else start
        upto = [e for e in events if _parse(e["ts"]) <= cutoff]
        m = compute_metrics(upto, _week(cutoff), now=_iso(cutoff)) if upto else compute_metrics([], _week(cutoff))
        _minutes_last_7_days(m, upto, cutoff)
        points.append({"day": day, "headline": m["headline"], "channels": m["channels"],
                       "learning": m["learning"], "guardrails": m["guardrails"]})
    last = _parse(events[-1]["ts"])
    final = compute_metrics(events, _week(last))
    _minutes_last_7_days(final, events, last)
    return {**final, "simulated": True, "timeline": points}


def _minutes_last_7_days(m: dict, events: list[dict], cutoff: datetime) -> None:
    """The demo shows a rolling week: calendar weeks reset to 0 every Monday mid-animation."""
    since = cutoff - timedelta(days=7)
    n = sum(1 for e in events if e.get("actor") == "user" and since < _parse(e["ts"]) <= cutoff)
    for h in m["headline"]:
        if h["name"].startswith("User minutes"):
            h.update(name="User minutes, last 7 days", value=n if events else None,
                     display=f"{n} min" if events else "—")
