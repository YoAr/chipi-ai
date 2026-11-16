"""Hiring demand index (spec v2 §6.2). Deterministic code; the Offer designer explains it, never computes it.

A 0-1 hiring demand index, not a claim about total market demand.

    index = (1 - w_r) * posting_signal + w_r * revealed        (sector signal: weight 0 until verified)
    w_r   = n / (n + REVEALED_HALF)                            n = sends logged for the service

Posting signal per skill or skill pair, from baseline-panel snapshots (last 90 days, each
posting counted once):
    base      = min(1, sqrt(share / SATURATION_SHARE))     share = matching postings / panel postings
    premium   = co-occurrence lift of a pair: + min(0.15, 0.05 * log2(lift)) when lift > 1 and count >= 3
    contract  = + 0.10 * share of matching postings whose employment type is freelance / contract / interim
                (fixed-term employment is still employment, so it doesn't count)
    trend     = x (1 + 0.2 * t), t in [-1, 1] from the last 30 days vs the 60 before (needs >= 45 days of snapshots)

Service index = max over the service's skill pairs x proof factor
(claimed 0.5, demonstrated 0.8, referenceable 1.0). Every input is returned so it can be shown.

Profile skill names are free text; each maps to the panel's skill tags through the same tagger
Scout uses (app/tagging.py). A skill with no tag has no market data, and the explanation says so.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from itertools import combinations

from .tagging import tag_skills

WEIGHTS = {"posting": 0.5, "sector": 0.2, "revealed": 0.3}  # spec defaults
SECTOR_VERIFIED = False      # CBS table not verified yet (spec open question 2): its weight goes to postings
WINDOW_DAYS = 90
TREND_DAYS = 30
MIN_TREND_HISTORY_DAYS = 45
REVEALED_HALF = 20           # sends at which revealed demand gets half the weight
PROOF_FACTOR = {"claimed": 0.5, "demonstrated": 0.8, "referenceable": 1.0}
CONTRACT_TYPES = {"freelance", "contract", "interim", "zzp"}   # Recruitee employment_type_code values
SATURATION_SHARE = 0.05      # a skill or pair in 5% of panel postings scores the full base


def _round(x: float | None, n: int = 3) -> float | None:
    return None if x is None else round(x, n)


def load_panel(conn, now: datetime | None = None) -> dict:
    """Panel postings in the window, one entry per posting (its latest snapshot)."""
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT day, posting_id, employment_type, skills_json FROM posting_snapshots WHERE day >= ? ORDER BY day",
        (start,)).fetchall()
    postings: dict[str, dict] = {}
    first_seen: dict[str, str] = {}
    for r in rows:
        first_seen.setdefault(r["posting_id"], r["day"])
        postings[r["posting_id"]] = {"skills": set(json.loads(r["skills_json"] or "[]")),
                                     "contract": (r["employment_type"] or "").lower() in CONTRACT_TYPES,
                                     "first_seen": first_seen[r["posting_id"]]}
    days = sorted({r["day"] for r in rows})
    return {"postings": list(postings.values()), "days": days, "window_days": WINDOW_DAYS}


def _trend(matching: list[dict], panel: dict, now: datetime) -> float | None:
    days = panel["days"]
    if len(days) < 2:
        return None
    span = (datetime.strptime(days[-1], "%Y-%m-%d") - datetime.strptime(days[0], "%Y-%m-%d")).days
    if span < MIN_TREND_HISTORY_DAYS:
        return None
    cut = (now - timedelta(days=TREND_DAYS)).strftime("%Y-%m-%d")
    recent_all = sum(1 for p in panel["postings"] if p["first_seen"] >= cut) or 1
    older_all = sum(1 for p in panel["postings"] if p["first_seen"] < cut) or 1
    recent = sum(1 for p in matching if p["first_seen"] >= cut) / recent_all
    older = sum(1 for p in matching if p["first_seen"] < cut) / older_all
    if recent + older == 0:
        return 0.0
    return max(-1.0, min(1.0, (recent - older) / max(recent, older)))


def _count(panel: dict, tag_groups: list[set[str]]) -> list[dict]:
    """Postings that match every group (a posting matches a group if it has any tag in it)."""
    return [p for p in panel["postings"] if all(p["skills"] & g for g in tag_groups)]


def posting_signal(panel: dict, tag_groups: list[set[str]], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    n = len(panel["postings"])
    if n == 0 or not all(tag_groups):
        return {"count": 0, "n": n, "signal": None}
    matching = _count(panel, tag_groups)
    count = len(matching)
    base = min(1.0, math.sqrt((count / n) / SATURATION_SHARE))
    lift = premium = None
    if len(tag_groups) == 2:
        pa, pb = (len(_count(panel, [g])) / n for g in tag_groups)
        if pa and pb:
            lift = (count / n) / (pa * pb)
            premium = min(0.15, 0.05 * math.log2(lift)) if lift > 1 and count >= 3 else 0.0
    contract_share = (sum(1 for p in matching if p["contract"]) / count) if count else 0.0
    trend = _trend(matching, panel, now)
    signal = (base + (premium or 0.0) + 0.10 * contract_share) * (1 + 0.2 * (trend or 0.0))
    return {"count": count, "n": n, "share": _round(count / n, 4), "base": _round(base), "lift": _round(lift, 2),
            "premium": _round(premium), "contract_share": _round(contract_share, 2), "trend": _round(trend, 2),
            "signal": _round(max(0.0, min(1.0, signal)))}


def weights(revealed_n: int) -> dict:
    # Starts at 0 and grows with sample size until it dominates (spec §6.2)
    w_r = revealed_n / (revealed_n + REVEALED_HALF) if WEIGHTS["revealed"] and revealed_n > 0 else 0.0
    sector = WEIGHTS["sector"] if SECTOR_VERIFIED else 0.0
    rest = 1 - w_r
    posting_share = WEIGHTS["posting"] / (WEIGHTS["posting"] + sector)
    return {"posting": _round(rest * posting_share), "sector": _round(rest * (1 - posting_share)),
            "revealed": _round(w_r)}


def combine(posting: float | None, revealed_rate: float | None = None, revealed_n: int = 0) -> tuple[float | None, dict]:
    w = weights(revealed_n if revealed_rate is not None else 0)
    if posting is None and not w["revealed"]:
        return None, w
    value = w["posting"] * (posting or 0.0) + w["revealed"] * (revealed_rate or 0.0)
    return _round(value), w


def skill_tags(name: str) -> set[str]:
    return set(tag_skills(name))


def market_table(conn, profile: dict, now: datetime | None = None, top: int = 30) -> dict:
    """Demand inputs per profile skill and skill pair, for the Offer designer to read."""
    panel = load_panel(conn, now)
    skills = [s["name"] for s in profile.get("skills", [])]
    tags = {s: skill_tags(s) for s in skills}
    singles = []
    for s in skills:
        sig = posting_signal(panel, [tags[s]], now)
        singles.append({"skills": [s], "tags": sorted(tags[s]), **sig})
    pairs = []
    measurable = [s for s in skills if tags[s]]
    for a, b in combinations(measurable, 2):
        if tags[a] == tags[b]:
            continue
        sig = posting_signal(panel, [tags[a], tags[b]], now)
        if sig["count"]:
            pairs.append({"skills": [a, b], "tags": [sorted(tags[a]), sorted(tags[b])], **sig})
    pairs.sort(key=lambda p: p["signal"] or 0, reverse=True)
    return {"panel_postings": len(panel["postings"]), "snapshot_days": len(panel["days"]),
            "first_day": panel["days"][0] if panel["days"] else None,
            "last_day": panel["days"][-1] if panel["days"] else None,
            "no_market_data": [s for s in skills if not tags[s]],
            "singles": singles, "pairs": pairs[:top], "weights": weights(0), "sector_verified": SECTOR_VERIFIED}


def service_index(conn, skills_used: list[str], proof_strength: str, revealed_rate: float | None = None,
                  revealed_n: int = 0, now: datetime | None = None) -> dict:
    """Index for one service, with every input and a plain explanation."""
    panel = load_panel(conn, now)
    tags = {s: skill_tags(s) for s in skills_used}
    candidates = []
    measurable = [s for s in skills_used if tags[s]]
    for a, b in combinations(measurable, 2):
        if tags[a] != tags[b]:
            candidates.append(([a, b], posting_signal(panel, [tags[a], tags[b]], now)))
    best_skills, best = max(candidates, key=lambda c: c[1]["signal"] or 0, default=(None, None))
    posting = best["signal"] if best else None
    factor = PROOF_FACTOR.get(proof_strength, 0.5)
    combined, w = combine(posting, revealed_rate, revealed_n)
    value = _round(combined * factor) if combined is not None else None
    return {
        "value": value, "posting_signal": posting, "best": {"skills": best_skills, **(best or {})} if best else None,
        "proof_strength": proof_strength, "proof_factor": factor, "weights": w,
        "revealed": {"rate": revealed_rate, "n": revealed_n},
        "no_market_data": [s for s in skills_used if not tags[s]],
        "panel": {"postings": len(panel["postings"]), "snapshot_days": len(panel["days"])},
        "explanation": explain(best_skills, best, factor, proof_strength, w, panel,
                               [s for s in skills_used if not tags[s]], value),
    }


def explain(best_skills, best, factor, strength, w, panel, unmeasured, value) -> str:
    if not panel["postings"]:
        return "No hiring data yet: the baseline panel hasn't been snapshotted. Run the panel snapshot first."
    if not best_skills or not best or best["signal"] is None:
        return ("Needs two skills with hiring data to score a combination."
                + (f" No market tag for: {', '.join(unmeasured)}." if unmeasured else ""))
    parts = [f"{' + '.join(best_skills)} appears in {best['count']} of {best['n']} panel postings "
             f"({best['share'] * 100:.1f}%)"]
    if best.get("lift") and best.get("premium"):
        parts.append(f"they're asked for together {best['lift']}x more often than chance (+{best['premium']})")
    if best.get("contract_share"):
        parts.append(f"{best['contract_share'] * 100:.0f}% of those are contract or freelance roles")
    parts.append("trend: not enough history yet" if best.get("trend") is None else f"30-day trend {best['trend']:+.2f}")
    text = "; ".join(parts) + f". Posting signal {best['signal']}"
    if w["revealed"]:
        text += f", blended with your own reply rate at weight {w['revealed']}"
    text += f", x {factor} for {strength} proof = {value}."
    if unmeasured:
        text += f" No market tag for: {', '.join(unmeasured)}."
    return text + " Hiring demand in the panel, not total market demand."
