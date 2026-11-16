"""The Milestone 8 flow through the API, using a local fake model."""

from app import learning
from app.ai import learner
from test_drafts_v2 import env  # noqa: F401


QUOTE = "Please scope another week for the migration."
TRANSCRIPT = f"Client: {QUOTE} The first pipeline is now running."


def review(quote=QUOTE):
    return learner.Review.model_validate({
        "sentiment": {"score": 2, "trend": "up", "evidence": [quote]},
        "working": [{"point": "Client asked for more work", "evidence": quote}],
        "not_working": [], "risks": [],
        "skill_proposals": [{"skill": "Data migration", "evidence": quote, "proof_strength": "claimed"}],
        "service_proposals": [{"change": "Offer a migration sprint", "evidence": quote, "rationale": "Client requested it"}],
    })


def book(client, store, lead):
    with store.conn:
        store.conn.execute("UPDATE leads SET status = 'ready' WHERE id = ?", (lead["id"],))
    for status in ("sent", "no_reply", "sent", "positive_reply", "meeting"):
        res = client.post(f"/api/leads/{lead['id']}/status", json={"status": status})
        assert res.status_code == 200, res.text
    return {"lead_id": lead["id"], "held_on": "2026-11-15", "transcript": TRANSCRIPT, "consent_confirmed": True}


def test_consent_evidence_and_independent_approval(env, monkeypatch):
    client, store, lead, _ = env
    body = book(client, store, lead)
    outputs = iter([review("made-up quote"), review()])
    calls = []

    def fake(role, settings, **kwargs):
        calls.append((role, kwargs))
        return next(outputs)

    monkeypatch.setattr(learner.ai, "structured", fake)
    assert client.post("/api/meetings", json={**body, "consent_confirmed": False}).status_code == 422
    assert not calls
    assert store.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0

    assert client.post("/api/meetings", json=body).status_code == 202
    assert client.get("/api/learnings/status").json()["status"] == "done"
    assert len(calls) == 2 and calls[-1][0] == "smart" and calls[-1][1]["store"] is False
    learning_row, = client.get("/api/learnings").json()["learnings"]
    assert "transcript" not in learning_row
    assert learning_row["sentiment"] == {"score": 2, "trend": "flat", "evidence": [QUOTE]}
    assert learning_row["approvals"] == {}
    assert learning.approved_service_changes(store) == []
    path = f"/api/learnings/{learning_row['id']}/approve"
    assert client.post(path, json={"kind": "service", "index": 2}).status_code == 404
    approved = client.post(path, json={"kind": "service", "index": 0}).json()
    assert client.post(path, json={"kind": "service", "index": 0}).json() == approved
    assert learning.approved_service_changes(store) == ["Offer a migration sprint"]
    learning_row, = client.get("/api/learnings").json()["learnings"]
    assert learning_row["approvals"] == {"service:0": approved["approved_at"]}

    data = client.get("/api/insights").json()
    assert data["segments"]["hook_type"] == [{
        "value": "funding", "sent": 1, "replies": 1, "positive_replies": 1,
        "reply_rate": 1.0, "positive_reply_rate": 1.0,
    }]
    assert {"stage": "meeting", "count": 1} in data["funnel"]["stages"]


def test_retry_reuses_transcript_and_requires_grounding(env, monkeypatch):
    client, store, lead, _ = env
    body = book(client, store, lead)
    monkeypatch.setattr(learner.ai, "structured", lambda *args, **kw: review("not in transcript"))
    assert client.post("/api/meetings", json=body).status_code == 202
    assert client.get("/api/learnings/status").json()["status"] == "error"
    assert client.get("/api/learnings").json()["learnings"] == []
    monkeypatch.setattr(learner.ai, "structured", lambda *args, **kw: review())
    assert client.post("/api/meetings", json=body).status_code == 202
    assert client.get("/api/learnings/status").json()["status"] == "done"
    assert store.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 1


def test_engagement_summary_triggers_service_redesign(env, monkeypatch):
    client, store, lead, _ = env
    body = book(client, store, lead)
    monkeypatch.setattr(learner.ai, "structured", lambda *args, **kw: review())
    assert client.post("/api/meetings", json=body).status_code == 202
    from app.ai import offer_designer

    redesigns = []
    monkeypatch.setattr(offer_designer, "run_design", lambda *args: redesigns.append(True))
    assert client.post("/api/learnings/engagement", json={"lead_id": lead["id"]}).status_code == 202
    assert redesigns == [True]
    summaries = client.get("/api/learnings").json()["learnings"]
    assert len(summaries) == 2 and summaries[0]["meeting_id"] is None


def test_private_reads_respect_demo_passcode(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app

    monkeypatch.setenv("MOCK_MODE", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEMO_PASSCODE", "test-passcode")
    with TestClient(create_app()) as client:
        for route in ("/api/learnings", "/api/learnings/status"):
            assert client.get(route).status_code == 401
            assert client.get(route, headers={"X-Demo-Passcode": "test-passcode"}).status_code == 200
