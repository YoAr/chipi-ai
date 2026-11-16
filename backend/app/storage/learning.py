"""After-approval learning: live events -> metrics -> wiki/learning.md and goal.md's DERIVED part.

Cheap, deterministic, no model calls: safe to run inline after a send, reply or win.

Why `metric_events` exists: `app/metrics.py` (and the simulation it was built against)
treat one `draft_approved` event carrying {channel, lead, template} as "approved and sent"
at the same moment. Live, those are two steps: the user approves (`draft_approved`, no
channel), then copies and sends it themselves (`draft_sent`, with channel). This view folds
each approved-then-sent pair into the single event metrics counts, timed at the send, so
live numbers are counted exactly like the simulation's. A `draft_sent` with no earlier
approval of the same draft is left as `draft_sent`, so the guardrail still counts it as an
unapproved send. The events file itself is never changed.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..metrics import compute_metrics
from . import events, wiki

LEARNING_PAGE = "wiki/learning.md"
FOLDED_APPROVAL = "draft_approved_folded"  # view-only type: ignored by metrics, still counts user minutes

# Lead source -> the channel names the simulation and the Metrics screen use (ui.tsx sourceLabel).
CHANNEL_LABELS = {
    "hn_whos_hiring": "HN Who's Hiring",
    "web_search": "Web search",
    "remotive": "Remotive",
    "remoteok": "RemoteOK",
    "seed": "Pasted link",
}

_DERIVED_RE = re.compile(r"<!--[\s\u2500-]*DERIVED[^>]*-->", re.IGNORECASE)  # same marker app.guards splits on
BLOCK_START = "<!-- LIVE NUMBERS: rewritten by code after every send, reply or win. -->"
BLOCK_END = "<!-- /LIVE NUMBERS -->"
_write_lock = threading.Lock()


def metric_channel(source: str | None, via: str | None) -> str:
    """The channel a send is counted under: where the lead came from, else how it was sent."""
    if source:
        return CHANNEL_LABELS.get(source, source)
    return "Direct email" if via == "email" else "Apply link"


def metric_events(evs: list[dict]) -> list[dict]:
    """The live log in metrics' vocabulary (see module docstring). Pure; order preserved."""
    approved: set = set()
    folded: set = set()
    for e in evs:
        p = e.get("payload") or {}
        if e.get("type") == "draft_approved" and p.get("draft_id"):
            approved.add(p["draft_id"])
        elif e.get("type") == "draft_sent" and p.get("draft_id") in approved:
            folded.add(p["draft_id"])
    out = []
    for e in evs:
        p = e.get("payload") or {}
        t = e.get("type")
        if t == "draft_approved" and p.get("draft_id") in folded and not p.get("channel"):
            e = {**e, "type": FOLDED_APPROVAL}
        elif t == "draft_sent" and p.get("draft_id") in folded:
            e = {**e, "type": "draft_approved"}
        out.append(e)
    return out


def _iso_week(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _ts(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def live_metrics(data_dir: Path, now: datetime | None = None) -> dict:
    """Metrics for the real events log, as of `now` (default: the current time)."""
    now = now or datetime.now(timezone.utc)
    evs = metric_events(events.read_events(data_dir))
    return compute_metrics(evs, _iso_week(now), now=_ts(now) if evs else None)


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{round(x * 100)}%"


def _channel_line(c: dict) -> str:
    reps = c["replies"]
    return (f"- {c['name']}: {c['sent']} counted, {reps} repl{'y' if reps == 1 else 'ies'} · "
            f"{_pct(c['value'])} (likely {_pct(c['low'])}–{_pct(c['high'])})")


def _cite(evs: list[dict]) -> str:
    if not evs:
        return "no events yet"
    return f"events.jsonl {evs[0].get('id')}–{evs[-1].get('id')}"


def learning_page(m: dict, evs: list[dict], now: datetime) -> str:
    sent = sum(c["sent"] for c in m["channels"])
    replies = sum(c["replies"] for c in m["channels"])
    lines = [
        "# What the numbers say",
        "",
        f"Rewritten by code after every send, reply or win. Last update {now:%Y-%m-%d %H:%M} UTC, "
        f"from {_cite(evs)}. Don't edit by hand: the next update replaces this page.",
        "",
        "## Reply rate by channel",
        "A send younger than 5 days with no reply yet is still in flight and isn't counted.",
        "",
    ]
    lines += [_channel_line(c) for c in m["channels"]] or ["- Nothing sent yet."]
    lines += ["", f"Total: {sent} counted send{'' if sent == 1 else 's'}, "
                  f"{replies} repl{'y' if replies == 1 else 'ies'}.", "", "## Lessons"]
    if m["learning"]:
        lines += [f"- {lsn['id']} {lsn['text']} ({lsn['status']})" for lsn in m["learning"]]
    else:
        lines.append("- None yet. A lesson needs 5+ counted sends on each channel and a clear gap.")
    income = next((h for h in m["headline"] if h["name"].startswith("Booked income")), None)
    g = m["guardrails"]
    lines += [
        "",
        "## Booked income",
        f"- {income['display'] if income else '—'}",
        "",
        "## Guardrails",
        f"- Unapproved sends: {g['unapproved_sends']}",
        f"- Quotes below the floor: {g['below_floor_quotes']}",
        f"- Unsourced claims: {g['unsourced_claims']}",
        f"- Duplicate contacts: {g['duplicate_contacts']}",
        "",
    ]
    return "\n".join(lines)


def goal_block(m: dict, now: datetime) -> str:
    sent = sum(c["sent"] for c in m["channels"])
    replies = sum(c["replies"] for c in m["channels"])
    income = next((h for h in m["headline"] if h["name"].startswith("Booked income")), None)
    rates = " · ".join(f"{c['name']} {_pct(c['value'])} (n={c['n']})" for c in m["channels"]) or "no sends yet"
    lessons = "; ".join(lsn["text"] for lsn in m["learning"]) or "none yet (needs 5+ counted sends per channel)"
    return "\n".join([
        BLOCK_START,
        f"## What the numbers say ({now:%Y-%m-%d})",
        f"- Counted sends: {sent} · replies: {replies} · booked: {income['display'] if income else '—'}",
        f"- Reply rate by channel: {rates}",
        f"- Lessons: {lessons}",
        "- Detail: wiki/learning.md",
        BLOCK_END,
    ])


def with_derived_block(goal_md: str, block: str) -> str | None:
    """goal.md with `block` placed in its DERIVED part; None if there is no DERIVED marker.

    Everything up to and including the marker line (the human-owned core) is kept byte for byte.
    """
    m = _DERIVED_RE.search(goal_md)
    if not m:
        return None
    eol = goal_md.find("\n", m.end())
    split = len(goal_md) if eol == -1 else eol + 1
    core, derived = goal_md[:split], goal_md[split:]
    start, end = derived.find(BLOCK_START), derived.find(BLOCK_END)
    if start != -1 and end > start:
        derived = derived[:start] + block + derived[end + len(BLOCK_END):]
    else:
        if derived and not derived.endswith("\n"):
            derived += "\n"
        derived += ("\n" if derived.strip() else "") + block + "\n"
    out = core + derived
    assert out.startswith(core)
    return out


def write_back(data_dir: Path, now: datetime | None = None) -> dict:
    """Recompute metrics from the log; rewrite wiki/learning.md and goal.md's live block. Returns the metrics."""
    now = now or datetime.now(timezone.utc)
    evs = events.read_events(data_dir)
    view = metric_events(evs)
    m = compute_metrics(view, _iso_week(now), now=_ts(now) if view else None)
    with _write_lock:
        wiki.write_page(data_dir, LEARNING_PAGE, learning_page(m, evs, now))
        try:
            current = wiki.read_page(data_dir, wiki.GOAL)
        except wiki.NotFound:  # no goal.md: nothing to update, and never create one
            current = None
        if current is not None:
            updated = with_derived_block(current, goal_block(m, now))
            if updated is not None and updated != current:
                wiki.write_page(data_dir, wiki.GOAL, updated)
    return m
