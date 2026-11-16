"""Outreach rates attributed to the first logged send for each lead."""

from collections import defaultdict

from . import tracker
from .storage.events import read_events

DIMENSIONS = ("service_id", "channel", "hook_type", "language")


def current(store) -> dict:
    sends = {}
    replies = set()
    positive = set()
    for event in read_events(store.data_dir):
        if event["type"] != "lead_status_changed":
            continue
        payload = event["payload"]
        lead_id = payload.get("lead_id")
        status = payload.get("to")
        if not lead_id:
            continue
        if status == "sent":
            sends.setdefault(lead_id, payload)
        if status in ("positive_reply", "not_interested", "meeting", "won"):
            replies.add(lead_id)
        if status in ("positive_reply", "meeting", "won"):
            positive.add(lead_id)

    segments = {}
    for dimension in DIMENSIONS:
        counts = defaultdict(lambda: {"sent": 0, "replies": 0, "positive_replies": 0})
        for lead_id, send in sends.items():
            group = counts[send.get(dimension) or "unknown"]
            group["sent"] += 1
            group["replies"] += lead_id in replies
            group["positive_replies"] += lead_id in positive
        segments[dimension] = [
            {"value": value, **group, "reply_rate": group["replies"] / group["sent"],
             "positive_reply_rate": group["positive_replies"] / group["sent"]}
            for value, group in sorted(counts.items())
        ]
    return {"funnel": tracker.funnel(store), "segments": segments}
