"""Deterministic demo event history (payload conventions: see app/metrics.py)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

DEFAULT_RATES = {"Direct email": 0.55, "HN Who's Hiring": 0.45, "Web search": 0.35, "RemoteOK": 0.15}
FOLLOWUP_DAYS = 5


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def simulate(days: int = 14, seed: int = 1, true_rates: dict[str, float] | None = None,
             sends_per_day: int = 3, start: str = "2026-11-02T08:00:00Z") -> list[dict]:
    rng = random.Random(seed)
    rates = true_rates or DEFAULT_RATES
    channels = list(rates)
    t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end = t0 + timedelta(days=days)
    raw: list[tuple[datetime, int, dict]] = []

    def add(ts: datetime, type_: str, actor: str, subject: str, payload: dict) -> None:
        raw.append((ts, len(raw), {"ts": _iso(ts), "type": type_, "actor": actor,
                                   "subject": subject, "payload": payload}))

    n = 0
    won = 0
    for day in range(days):
        for k in range(sends_per_day):
            n += 1
            lead = f"lead-{n:03d}"
            ch = rng.choice(channels)
            t = t0 + timedelta(days=day, minutes=20 * k)
            subj = f"lead:{lead}"
            add(t, "lead_found", "agent", subj, {"channel": ch, "lead": lead})
            add(t + timedelta(minutes=2), "lead_verdict", "user", subj, {"verdict": "good", "lead": lead})
            add(t + timedelta(minutes=5), "draft_created", "agent", f"draft:{n}", {"lead": lead, "channel": ch})
            sent = t + timedelta(minutes=9)
            add(sent, "draft_approved", "user", f"draft:{n}",
                {"channel": ch, "lead": lead, "template": f"v{1 + n % 3}"})
            if rng.random() < rates[ch]:
                rt = sent + timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 8))
                add(rt, "reply_received", "world", subj, {"channel": ch, "lead": lead})
                if rng.random() < 0.2 or (won == 0 and n == sends_per_day * days):
                    won += 1
                    add(rt + timedelta(days=2), "contract_won", "world", subj,
                        {"lead": lead, "amount": rng.choice([3000, 4500, 6000]), "goal": 12000})
            else:
                add(sent + timedelta(days=FOLLOWUP_DAYS), "no_reply_timeout", "world", subj,
                    {"channel": ch, "lead": lead})

    raw = [r for r in raw if r[0] <= end]
    raw.sort(key=lambda r: (r[0], r[1]))
    events = []
    for i, (_, _, e) in enumerate(raw, 1):
        events.append({"id": f"ev_{i:06d}", **e})
    return events
