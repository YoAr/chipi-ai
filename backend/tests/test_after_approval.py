"""After approval: mark sent, got a reply, won it, and the Learner write-back (real and mock mode)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings
from app.main import create_app
from app.metrics import compute_metrics
from app.storage import events, learning, queue

FIXTURES = REPO_ROOT / "fixtures"
TOKEN = "after-approval-token"
LATER = datetime.now(timezone.utc) + timedelta(days=6)  # past the 5-day in-flight window


def _settings(tmp_path, mock: bool) -> Settings:
    return Settings(
        mock_mode=mock, data_dir=tmp_path / "data", fixtures_dir=FIXTURES, mcp_token=TOKEN,
        cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "no-dist",
        landing_file=tmp_path / "no-landing.html",
    )


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(_settings(tmp_path, mock=False))) as c:
        yield c


@pytest.fixture()
def mock_client(tmp_path):
    with TestClient(create_app(_settings(tmp_path, mock=True))) as c:
        yield c


def _fixture_lead(n: int) -> dict:
    lead = json.loads((FIXTURES / "leads.json").read_text())["leads"][n]
    return {k: v for k, v in lead.items() if k != "id"}


def _approved_draft(client, n: int = 3, channel: str = "email", to: str = "hiring@example.com") -> tuple[dict, dict]:
    """A lead from the fixture (n=3 is react-rebuild, source remoteok) with an approved draft."""
    store = client.app.state.store
    lead = store.upsert_leads([_fixture_lead(n)])[0]
    draft = store.add_draft({"lead_id": lead["id"], "channel": channel, "to": to, "subject": "Hi",
                             "body": "Draft text", "template_version": "v2"})
    res = client.post(f"/api/drafts/{draft['id']}/approve", json={"final_text": "Final text"})
    assert res.status_code == 200 and res.json()["status"] == "approved"
    return lead, res.json()


def _events(client, type_: str | None = None) -> list[dict]:
    evs = events.read_events(client.app.state.store.data_dir)
    return [e for e in evs if type_ is None or e["type"] == type_]


def _channel(m: dict, name: str) -> dict | None:
    return next((c for c in m["channels"] if c["name"] == name), None)


def _metrics_later(client) -> dict:
    return learning.live_metrics(client.app.state.store.data_dir, now=LATER)


# Mark sent -----------------------------------------------------------------
def test_cannot_mark_sent_before_approve(client):
    store = client.app.state.store
    lead = store.upsert_leads([_fixture_lead(3)])[0]
    pending = store.add_draft({"lead_id": lead["id"], "body": "x"})
    res = client.post(f"/api/drafts/{pending['id']}/sent")
    assert res.status_code == 422 and res.json()["error"]["code"] == "blocked"
    assert "approv" in res.json()["error"]["message"]

    rejected = store.add_draft({"body": "y"})
    client.post(f"/api/drafts/{rejected['id']}/reject", json={"note": "no"})
    assert client.post(f"/api/drafts/{rejected['id']}/sent").status_code == 422

    assert store.drafts()["drafts"][-1]["status"] in ("pending", "rejected")
    assert _events(client, "draft_sent") == []
    assert client.post("/api/drafts/dr_nope/sent").status_code == 404


def test_mark_sent_finishes_queue_and_counts_a_send_for_the_lead_source(client):
    lead, draft = _approved_draft(client)
    store = client.app.state.store
    assert len(queue.pending(store.conn)) == 1

    res = client.post(f"/api/drafts/{draft['id']}/sent")
    assert res.status_code == 200
    out = res.json()
    assert out["status"] == "sent" and out["sent_at"] and out["approved_at"]
    assert queue.pending(store.conn) == []
    row = store.conn.execute("SELECT status FROM queue WHERE ref = ?", (draft["id"],)).fetchone()
    assert row["status"] == "done"
    assert client.get("/api/leads").json()["leads"][0]["status"] == "sent"

    (sent,) = _events(client, "draft_sent")
    assert sent["actor"] == "user"
    assert sent["payload"] == {
        "draft_id": draft["id"], "queue_id": sent["payload"]["queue_id"], "lead_id": lead["id"],
        "lead": "react-rebuild", "channel": "RemoteOK", "via": "email", "template": "v2",
    }
    # Counted the way the simulation's events are counted: one send on the lead's channel
    m = _metrics_later(client)
    assert _channel(m, "RemoteOK") == {"name": "RemoteOK", "sent": 1, "replies": 0, "value": 0.333,
                                      "low": _channel(m, "RemoteOK")["low"],
                                      "high": _channel(m, "RemoteOK")["high"], "n": 1}
    assert m["guardrails"] == {"unapproved_sends": 0, "below_floor_quotes": 0,
                               "unsourced_claims": 0, "duplicate_contacts": 0}
    # Today it is still in flight: listed, but not counted yet
    live = client.get("/api/metrics").json()
    assert _channel(live, "RemoteOK")["sent"] == 0


def test_approval_alone_is_not_a_send(client):
    _approved_draft(client)
    assert _metrics_later(client)["channels"] == []


def test_mark_sent_twice_counts_once(client):
    _, draft = _approved_draft(client)
    first = client.post(f"/api/drafts/{draft['id']}/sent").json()
    second = client.post(f"/api/drafts/{draft['id']}/sent")
    assert second.status_code == 200 and second.json()["sent_at"] == first["sent_at"]
    assert len(_events(client, "draft_sent")) == 1
    assert _channel(_metrics_later(client), "RemoteOK")["sent"] == 1


def test_apply_link_draft_channel(client):
    _, draft = _approved_draft(client, n=0, channel="apply_link", to="https://example.com/apply")
    client.post(f"/api/drafts/{draft['id']}/sent")
    (sent,) = _events(client, "draft_sent")
    assert sent["payload"]["via"] == "apply_link" and sent["payload"]["channel"] == "HN Who's Hiring"


# Got a reply / Won it --------------------------------------------------------
def test_reply_raises_the_reply_rate_and_moves_the_lead(client):
    lead, draft = _approved_draft(client)
    client.post(f"/api/drafts/{draft['id']}/sent")
    before = _channel(_metrics_later(client), "RemoteOK")

    res = client.post(f"/api/leads/{lead['id']}/replied", json={})
    assert res.status_code == 200 and res.json()["status"] == "replied"
    after = _channel(_metrics_later(client), "RemoteOK")
    assert (before["sent"], before["replies"]) == (1, 0)
    assert (after["sent"], after["replies"]) == (1, 1)
    assert after["value"] > before["value"]
    # Live metrics (today) count it too: a young send with a reply is no longer in flight
    assert _channel(client.get("/api/metrics").json(), "RemoteOK")["replies"] == 1

    (reply,) = _events(client, "reply_received")
    assert reply["actor"] == "world"
    assert reply["payload"] == {"lead": "react-rebuild", "lead_id": lead["id"], "draft_id": draft["id"],
                                "channel": "RemoteOK", "reported_by": "user"}
    cols = {c["name"]: c["items"] for c in client.get("/api/pipeline").json()["columns"]}
    assert [i["nickname"] for i in cols["Replied"]] == ["react-rebuild"] and cols["Sent"] == []


def test_reply_and_win_twice_count_once(client):
    lead, draft = _approved_draft(client)
    client.post(f"/api/drafts/{draft['id']}/sent")
    for _ in range(2):
        assert client.post(f"/api/leads/{lead['id']}/replied").status_code == 200
    for _ in range(2):
        assert client.post(f"/api/leads/{lead['id']}/won", json={"amount": 3000}).status_code == 200
    assert client.post(f"/api/leads/{lead['id']}/replied").json()["status"] == "won"  # no step back
    assert len(_events(client, "reply_received")) == 1
    assert len(_events(client, "contract_won")) == 1
    assert _channel(_metrics_later(client), "RemoteOK")["replies"] == 1


def test_won_without_reply_implies_one_and_books_income(client):
    client.put("/api/persona", json={"income_goal_monthly": 12000})
    lead, draft = _approved_draft(client)
    client.post(f"/api/drafts/{draft['id']}/sent")
    res = client.post(f"/api/leads/{lead['id']}/won", json={"amount": 6000})
    assert res.status_code == 200 and res.json()["status"] == "won"

    (reply,) = _events(client, "reply_received")
    assert reply["payload"]["implied_by"] == "contract_won"
    (won,) = _events(client, "contract_won")
    assert won["payload"]["amount"] == 6000 and won["payload"]["goal"] == 12000
    assert won["payload"]["lead"] == "react-rebuild"
    income = client.get("/api/metrics").json()["headline"][0]
    assert income["value"] == 0.5 and income["display"] == "50% of monthly goal"
    cols = {c["name"]: c["items"] for c in client.get("/api/pipeline").json()["columns"]}
    assert [i["nickname"] for i in cols["Active"]] == ["react-rebuild"]


def test_reply_or_win_before_sending_is_refused(client):
    lead, _ = _approved_draft(client)  # approved, not sent
    for path in ("replied", "won"):
        res = client.post(f"/api/leads/{lead['id']}/{path}")
        assert res.status_code == 409 and "mark the message sent" in res.json()["error"]["message"]
    assert _events(client, "reply_received") == [] and _events(client, "contract_won") == []
    assert client.post("/api/leads/ld_nope/replied").status_code == 404
    assert client.post(f"/api/leads/{lead['id']}/won", json={"amount": -5}).status_code == 422


# Learner write-back ----------------------------------------------------------
def _split_goal(text: str) -> tuple[str, str]:
    # The marker is the comment that starts with DERIVED (the fixed-core comment also mentions it)
    marker = learning._DERIVED_RE.search(text).start()
    eol = text.index("\n", marker) + 1
    return text[:eol], text[eol:]


def test_learning_page_and_goal_derived_written_core_untouched(client, approve_profile):
    data = client.app.state.store.data_dir
    goal_before = (data / "goal.md").read_text()
    core_before, derived_before = _split_goal(goal_before)

    lead, draft = _approved_draft(client)
    client.post(f"/api/drafts/{draft['id']}/sent")
    page = (data / "wiki" / "learning.md").read_text()
    assert page.startswith("# What the numbers say") and "RemoteOK" in page and "Unapproved sends: 0" in page

    client.post(f"/api/leads/{lead['id']}/replied")
    page = (data / "wiki" / "learning.md").read_text()
    assert "RemoteOK: 1 counted, 1 reply" in page

    goal_after = (data / "goal.md").read_text()
    core_after, derived_after = _split_goal(goal_after)
    assert core_after == core_before  # the human-owned core, byte for byte
    assert derived_after.startswith(derived_before.rstrip("\n"))  # the existing plan is kept
    assert derived_after.count(learning.BLOCK_START) == 1 and derived_after.count(learning.BLOCK_END) == 1
    assert "Reply rate by channel: RemoteOK" in derived_after
    # The rate floor isn't in goal.md any more: it comes from the approved profile, and write-back leaves it be
    assert "Rate floor: set in your approved profile" in core_after
    assert client.get("/api/fit").json()["rate"]["floor"] is None
    approve_profile(client, 100)
    assert client.get("/api/fit").json()["rate"]["floor"] == 100


def test_write_back_replaces_its_block_and_never_writes_without_a_marker(tmp_path):
    text = "# Goal\n\n<!-- FIXED CORE: never edit above the DERIVED line. -->\n\n- Rate floor: set in your approved profile.\n\n<!-- ── DERIVED ── -->\n\n## Current plan\n- x\n"
    once = learning.with_derived_block(text, f"{learning.BLOCK_START}\nA\n{learning.BLOCK_END}")
    twice = learning.with_derived_block(once, f"{learning.BLOCK_START}\nB\n{learning.BLOCK_END}")
    assert once.startswith(text.split("## Current plan")[0]) and "\nA\n" in once
    assert "\nB\n" in twice and "\nA\n" not in twice and twice.count(learning.BLOCK_START) == 1
    assert twice.startswith(text)
    assert learning.with_derived_block("# Goal\n<!-- never edit above the DERIVED line -->\n", "x") is None


def test_write_back_failure_does_not_fail_the_click(client, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(learning, "write_back", boom)
    _, draft = _approved_draft(client)
    assert client.post(f"/api/drafts/{draft['id']}/sent").json()["status"] == "sent"


# The metrics view ---------------------------------------------------------------
def _ev(i, type_, payload, actor="user", ts="2026-11-01T08:00:00Z"):
    return {"id": f"ev_{i:04d}", "ts": ts, "type": type_, "actor": actor, "subject": "x", "payload": payload}


def test_metric_events_folds_approve_and_send_and_keeps_unapproved_sends_visible():
    evs = [
        _ev(1, "draft_approved", {"draft_id": "d1", "lead": "a"}),
        _ev(2, "draft_sent", {"draft_id": "d1", "lead": "a", "channel": "RemoteOK"}),
        _ev(3, "draft_approved", {"draft_id": "d2", "lead": "b"}),  # approved, not sent yet
        _ev(4, "draft_sent", {"draft_id": "d3", "lead": "c", "channel": "RemoteOK"}),  # never approved
    ]
    view = learning.metric_events(evs)
    assert [e["type"] for e in view] == [learning.FOLDED_APPROVAL, "draft_approved", "draft_approved", "draft_sent"]
    m = compute_metrics(view, "2026-W45", now="2026-11-10T08:00:00Z")
    assert m["channels"] == [{"name": "RemoteOK", "sent": 1, "replies": 0, "value": 0.333,
                              "low": m["channels"][0]["low"], "high": m["channels"][0]["high"], "n": 1}]
    assert m["guardrails"]["unapproved_sends"] == 1 and m["guardrails"]["duplicate_contacts"] == 0
    # Simulation-style events pass through untouched
    sim = [_ev(1, "draft_approved", {"channel": "A", "lead": "l1", "template": "v1"})]
    assert learning.metric_events(sim) == sim


# Mock mode ---------------------------------------------------------------------
def test_mock_mode_same_rules(mock_client):
    c = mock_client
    assert c.post("/api/drafts/dr_001/sent").status_code == 422  # still pending
    assert c.post("/api/drafts/dr_001/approve", json={"final_text": "Final"}).json()["status"] == "approved"
    first = c.post("/api/drafts/dr_001/sent").json()
    assert first["status"] == "sent" and first["sent_at"]
    assert c.post("/api/drafts/dr_001/sent").json()["sent_at"] == first["sent_at"]

    # data-pipeline (ld_003) is only drafted on the sample board: nothing sent yet
    assert c.post("/api/leads/ld_003/replied").status_code == 409
    # react-rebuild (ld_004) sits in Sent on the sample board
    assert c.post("/api/leads/ld_004/replied").json()["status"] == "replied"
    assert c.post("/api/leads/ld_004/replied").json()["status"] == "replied"
    cols = {col["name"]: col["items"] for col in c.get("/api/pipeline").json()["columns"]}
    assert [i["nickname"] for i in cols["Replied"]].count("react-rebuild") == 1
    assert all(i["nickname"] != "react-rebuild" for i in cols["Sent"])

    assert c.post("/api/leads/ld_004/won", json={"amount": 4500}).json()["status"] == "won"
    cols = {col["name"]: col["items"] for col in c.get("/api/pipeline").json()["columns"]}
    assert [i["nickname"] for i in cols["Active"]][0] == "react-rebuild"
    assert all(i["nickname"] != "react-rebuild" for i in cols["Replied"])
