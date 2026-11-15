"""Metrics computed from the append-only event log (stdlib only, deterministic).

Payload conventions used here (see docs/events.md for the envelope):
  draft_approved.payload  = {channel, lead, template}
  reply_received.payload  = {channel, lead}
  no_reply_timeout.payload = {channel, lead}
  contract_won.payload    = {lead, amount, goal?}   (goal = monthly income goal)

Guardrail rules (0 when no signalling events exist):
  unapproved_sends   = `draft_sent` events with no earlier `draft_approved` for the same lead.
  below_floor_quotes = `rate_quoted` events with payload.rate < payload.floor, plus
                       `verifier_failed` events with payload.reason == "below_floor".
  unsourced_claims   = `verifier_failed` events with payload.reason == "unsourced_claim".
  duplicate_contacts = `draft_approved` events for a lead that already had an approved draft.

Right-censoring: an approved draft younger than `followup_days` with no reply yet
is still "in flight" and counts in neither `sent` nor `replies`.

Learning: for each ordered pair of channels with >=5 effective sends, propose
"A replies more than B" when P(A > B) >= 0.8, Bonferroni-tightened over the number
of eligible pairs so pure noise rarely yields a lesson.

Headline: income = sum of contract_won amounts in the calendar month of `now`
divided by payload.goal (null when no goal is known). User minutes = number of
events with actor == "user" in the ISO week `week`, at 1 minute each (rough proxy).
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from itertools import permutations


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _beta_quantile(a: float, b: float, q: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _betainc(a, b, mid) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def rate_with_range(successes: int, trials: int, level: float = 0.8) -> tuple[float, float, float]:
    """(value, low, high): posterior mean under Beta(1,1) and central credible interval."""
    a, b = successes + 1, trials - successes + 1
    tail = (1.0 - level) / 2
    value = a / (a + b)
    low = _beta_quantile(a, b, tail)
    high = _beta_quantile(a, b, 1.0 - tail)
    return round(value, 3), round(low, 3), round(high, 3)


def p_better(a_succ: int, a_n: int, b_succ: int, b_n: int, draws: int = 20000, seed: int = 7) -> float:
    """P(rate_A > rate_B) under Beta posteriors (seeded Monte Carlo)."""
    rng = random.Random(seed)
    a1, a2 = a_succ + 1, a_n - a_succ + 1
    b1, b2 = b_succ + 1, b_n - b_succ + 1
    wins = 0
    for _ in range(draws):
        if rng.betavariate(a1, a2) > rng.betavariate(b1, b2):
            wins += 1
    return wins / draws


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _channel_stats(events: list[dict], now: datetime, followup_days: int) -> dict[str, tuple[int, int]]:
    replies = {}
    for e in events:
        if e.get("type") == "reply_received":
            p = e.get("payload") or {}
            replies[(p.get("channel"), p.get("lead"))] = True
    stats: dict[str, list[int]] = {}
    seen: set[tuple] = set()
    for e in events:
        if e.get("type") != "draft_approved":
            continue
        p = e.get("payload") or {}
        ch, lead = p.get("channel"), p.get("lead")
        if not ch or (ch, lead) in seen:
            continue
        seen.add((ch, lead))
        got = (ch, lead) in replies
        young = now - _parse(e["ts"]) < timedelta(days=followup_days)
        s = stats.setdefault(ch, [0, 0])
        if young and not got:
            continue
        s[0] += 1
        s[1] += 1 if got else 0
    return {k: (v[0], v[1]) for k, v in stats.items()}


def _guardrails(events: list[dict]) -> dict:
    approved: set = set()
    out = {"unapproved_sends": 0, "below_floor_quotes": 0, "unsourced_claims": 0, "duplicate_contacts": 0}
    for e in events:
        t, p = e.get("type"), e.get("payload") or {}
        if t == "draft_approved":
            if p.get("lead") in approved:
                out["duplicate_contacts"] += 1
            approved.add(p.get("lead"))
        elif t == "draft_sent" and p.get("lead") not in approved:
            out["unapproved_sends"] += 1
        elif t == "rate_quoted":
            try:
                if float(p["rate"]) < float(p["floor"]):
                    out["below_floor_quotes"] += 1
            except (KeyError, TypeError, ValueError):
                pass
        elif t == "verifier_failed":
            if p.get("reason") == "below_floor":
                out["below_floor_quotes"] += 1
            elif p.get("reason") == "unsourced_claim":
                out["unsourced_claims"] += 1
    return out


def _iso_week(ts: datetime) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def compute_metrics(events: list[dict], week: str, now: str | None = None, followup_days: int = 5) -> dict:
    if now is not None:
        now_dt = _parse(now)
    elif events:
        now_dt = max(_parse(e["ts"]) for e in events)
    else:
        now_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)

    # headline
    won = [e for e in events if e.get("type") == "contract_won"
           and (_parse(e["ts"]).year, _parse(e["ts"]).month) == (now_dt.year, now_dt.month)]
    goal = next((float(e["payload"]["goal"]) for e in reversed(won) if (e.get("payload") or {}).get("goal")), None)
    if goal:
        income = sum(float((e.get("payload") or {}).get("amount") or 0) for e in won) / goal
        income_h = {"value": round(income, 3), "display": f"{round(income * 100)}% of monthly goal"}
    else:
        income_h = {"value": None, "display": "—"}
    user_events = [e for e in events if e.get("actor") == "user" and _iso_week(_parse(e["ts"])) == week]
    minutes = len(user_events)
    headline = [
        # "direction" is the way each number SHOULD move (the contract's up|down), not a trend.
        {"name": "Booked income vs goal", **income_h, "low": None, "high": None, "n": None, "direction": "up"},
        {"name": "User minutes this week", "value": minutes if events else None,
         "low": None, "high": None, "n": None, "direction": "down",
         "display": f"{minutes} min" if events else "—"},
    ]

    # channels
    stats = _channel_stats(events, now_dt, followup_days)
    channels = []
    for name, (sent, reps) in stats.items():
        v, lo, hi = rate_with_range(reps, sent)
        channels.append({"name": name, "sent": sent, "replies": reps, "value": v, "low": lo, "high": hi, "n": sent})

    # learning
    # Multiple-comparison guard: with k eligible channels there are k(k-1)/2 pairs, so the
    # 0.8 bar is tightened to 1 - 0.2/pairs (Bonferroni) to avoid inventing lessons from noise.
    eligible = {k: v for k, v in stats.items() if v[0] >= 5}
    pairs = max(1, len(eligible) * (len(eligible) - 1) // 2)
    bar = 1.0 - (1.0 - 0.8) / pairs
    learning = []
    for (an, (asent, areps)), (bn, (bsent, breps)) in permutations(eligible.items(), 2):
        if p_better(areps, asent, breps, bsent) >= bar:
            learning.append({"id": f"L-{len(learning) + 1:03d}",
                             "text": f"{an} replies more than {bn}", "status": "proposed"})

    return {"week": week, "headline": headline, "channels": channels,
            "guardrails": _guardrails(events), "learning": learning}
