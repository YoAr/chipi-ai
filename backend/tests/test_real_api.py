"""The API in real mode (MOCK_MODE=0): RealStore on a temporary data folder."""

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings
from app.main import create_app

TOKEN = "real-test-token"


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        mock_mode=False,
        data_dir=tmp_path / "data",
        fixtures_dir=REPO_ROOT / "fixtures",
        mcp_token=TOKEN,
        cors_origins=(),
        mock_stream_delay=0,
        web_dist=tmp_path / "no-dist",
        landing_file=tmp_path / "no-landing.html",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_starts_empty_and_seeded(client, tmp_path):
    assert client.get("/health").json()["mode"] == "real"
    assert client.get("/api/state").json()["mode"] == "real"
    assert client.get("/api/leads").json()["leads"] == []
    assert (tmp_path / "data" / "goal.md").exists()  # seeded from repo data/
    assert client.get("/api/fit").json()["rate"]["floor"] == 100  # read from goal.md


def test_seed_then_verdict_is_recorded_as_events(client, tmp_path):
    lead = client.post("/api/seeds", json={"url": "https://example.com/jobs/1"}).json()
    assert client.post(f"/api/leads/{lead['id']}/verdict", json={"verdict": "useful"}).json()["status"] == "useful"
    events = (tmp_path / "data" / "events.jsonl").read_text()
    assert '"seed_added"' in events and '"lead_verdict"' in events


def test_cv_upload_saves_file_and_text(client, tmp_path):
    res = client.post("/api/cv", files={"file": ("Sam CV.txt", b"Sam. Python and FastAPI, 5 years.", "text/plain")})
    assert res.status_code == 200
    raw = sorted(p.name for p in (tmp_path / "data" / "raw").iterdir() if p.name != ".gitkeep")
    assert len(raw) == 2  # the upload and its extracted text


def test_metrics_computed_from_events(client):
    body = client.get("/api/metrics").json()
    assert body["guardrails"]["unapproved_sends"] == 0
    assert "simulated" not in body


def test_chat_says_it_is_not_connected_yet(client):
    res = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert '"type": "error"' in res.text and '"type": "done"' in res.text


def test_reset_and_mcp_need_the_token(client):
    assert client.post("/api/reset").status_code == 401
    assert client.post("/api/reset", headers={"Authorization": f"Bearer {TOKEN}"}).json() == {"ok": True}
    assert client.post("/mcp", json={}).status_code == 401
