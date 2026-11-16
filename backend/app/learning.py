"""Consent, meeting review jobs and proposal approval."""

import json
import threading
from datetime import datetime, timezone
from uuid import uuid4

from . import insights, jobs, profiles
from .ai import client as ai, learner
from .mock_store import NotFound
from .storage.db import WRITE_LOCK

REVIEW_GROUPS = ("working", "not_working", "risks", "skill_proposals", "service_proposals")

JOB = "learner"
START_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def engagements(store) -> list[dict]:
    rows = store.conn.execute(
        "SELECT id, title, company, status, "
        "EXISTS(SELECT 1 FROM learnings WHERE lead_id = leads.id AND meeting_id IS NOT NULL) AS has_reviews "
        "FROM leads WHERE status IN ('meeting', 'won') OR "
        "EXISTS(SELECT 1 FROM meetings WHERE lead_id = leads.id) ORDER BY company, title"
    )
    return [dict(row) for row in rows]


def start_meeting(store, lead_id: str, held_on: str, transcript: str) -> str | None:
    lead = store.lead(lead_id)
    if lead["status"] not in ("meeting", "won"):
        raise ValueError("Choose a lead with a booked meeting or a won engagement")
    with START_LOCK:
        if not jobs.begin(store, JOB, step="analyzing", message="Reviewing your meeting"):
            return None
        meeting_id = uuid4().hex
        try:
            with WRITE_LOCK, store.conn:
                previous = store.conn.execute(
                    "SELECT id FROM meetings WHERE lead_id = ? AND held_on = ? AND transcript = ? "
                    "AND NOT EXISTS(SELECT 1 FROM learnings WHERE meeting_id = meetings.id) LIMIT 1",
                    (lead_id, held_on, transcript),
                ).fetchone()
                if previous:
                    meeting_id = previous["id"]
                else:
                    store.conn.execute(
                        "INSERT INTO meetings VALUES (?, ?, ?, ?, 1)", (meeting_id, lead_id, held_on, transcript)
                    )
        except Exception:
            jobs.fail(store, JOB, "Could not save the meeting")
            raise
    return meeting_id


def _reviews(store, lead_id: str) -> list[dict]:
    return [json.loads(row["json"]) for row in store.conn.execute(
        "SELECT learnings.json FROM learnings JOIN meetings ON meetings.id = learnings.meeting_id "
        "WHERE learnings.lead_id = ? ORDER BY meetings.held_on DESC, learnings.created_at DESC LIMIT 10", (lead_id,)
    )]


def start_summary(store, lead_id: str) -> bool:
    store.lead(lead_id)
    if not _reviews(store, lead_id):
        raise ValueError("Analyze a meeting for this engagement first")
    with START_LOCK:
        return jobs.begin(store, JOB, step="summarizing", message="Reviewing the completed engagement")


def run(store, settings, lead_id: str, meeting_id: str | None = None) -> None:
    lead = store.lead(lead_id)
    previous = _reviews(store, lead_id)
    payload = {
        "engagement": {key: lead.get(key) for key in ("title", "company", "status")},
        "profile_skills": [item["name"] for item in (profiles.approved_profile(store) or {}).get("skills", [])],
        "outreach": insights.current(store)["segments"],
    }
    if meeting_id:
        meeting = store.conn.execute(
            "SELECT transcript, held_on FROM meetings WHERE id = ? AND consent_confirmed = 1", (meeting_id,)
        ).fetchone()
        payload["meeting"] = dict(meeting)
        payload["previous_sentiment"] = [item["sentiment"]["score"] for item in previous]
        sources = [meeting["transcript"]]
    else:
        payload["meeting_reviews"] = previous
        sources = []
        for item in previous:
            sources.extend(item["sentiment"]["evidence"])
            sources.extend(fact["evidence"] for group in REVIEW_GROUPS for fact in item[group])

    result = learner.analyze(settings, payload, sources).model_dump()
    if meeting_id and not previous:
        result["sentiment"]["trend"] = "flat"
    learning_id = uuid4().hex
    created_at = _now()
    result["provenance"] = {"model": ai.model_for("smart", settings), "prompt_version": learner.PROMPT_VERSION,
                            "input_ref": meeting_id or lead_id, "ts": created_at}
    with WRITE_LOCK, store.conn:
        store.conn.execute("INSERT INTO learnings VALUES (?, ?, ?, ?, ?)",
                           (learning_id, lead_id, meeting_id, json.dumps(result, ensure_ascii=False), created_at))

    if not meeting_id and profiles.approved_profile(store):
        from .ai import offer_designer
        if jobs.begin(store, offer_designer.JOB, step="demand", message="Redesigning services after the engagement"):
            jobs.run(store, offer_designer.JOB, offer_designer.run_design, store, settings)
    jobs.done(store, JOB, "Review ready", {"learning_id": learning_id, "meeting_id": meeting_id})


def _out(row, approvals: dict) -> dict:
    return {"id": row["id"], "lead_id": row["lead_id"], "meeting_id": row["meeting_id"], "held_on": row["held_on"],
            "company": row["company"], "created_at": row["created_at"], **json.loads(row["json"]),
            "approvals": approvals}


def current(store) -> list[dict]:
    approvals = {}
    for row in store.conn.execute("SELECT * FROM learning_approvals"):
        approvals.setdefault(row["learning_id"], {})[f'{row["kind"]}:{row["proposal_index"]}'] = row["approved_at"]
    return [_out(row, approvals.get(row["id"], {})) for row in store.conn.execute(
        "SELECT l.*, m.held_on, leads.company FROM learnings l LEFT JOIN meetings m ON m.id = l.meeting_id "
        "LEFT JOIN leads ON leads.id = l.lead_id ORDER BY l.created_at DESC"
    )]


def approve(store, learning_id: str, kind: str, index: int) -> dict:
    with WRITE_LOCK, store.conn:
        row = store.conn.execute("SELECT json FROM learnings WHERE id = ?", (learning_id,)).fetchone()
        if row is None or not 0 <= index < len(json.loads(row["json"])[f"{kind}_proposals"]):
            raise NotFound("Learning proposal not found")
        store.conn.execute("INSERT OR IGNORE INTO learning_approvals VALUES (?, ?, ?, ?)",
                           (learning_id, kind, index, _now()))
        saved = store.conn.execute(
            "SELECT approved_at FROM learning_approvals WHERE learning_id = ? AND kind = ? AND proposal_index = ?",
            (learning_id, kind, index)
        ).fetchone()
    return {"approved_at": saved["approved_at"]}


def approved_service_changes(store) -> list[str]:
    return [json.loads(row["json"])["service_proposals"][row["proposal_index"]]["change"]
            for row in store.conn.execute(
                "SELECT l.json, a.proposal_index FROM learning_approvals a "
                "JOIN learnings l ON l.id = a.learning_id WHERE a.kind = 'service' "
                "ORDER BY a.approved_at DESC LIMIT 20"
            )]
