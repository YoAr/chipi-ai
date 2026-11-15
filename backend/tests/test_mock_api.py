import json

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings
from app.main import create_app

TOKEN = "test-token"


@pytest.fixture()
def client() -> TestClient:
    settings = Settings(
        mock_mode=True,
        data_dir=REPO_ROOT / "runtime-data",
        fixtures_dir=REPO_ROOT / "fixtures",
        mcp_token=TOKEN,
        cors_origins=("http://localhost:5173",),
        mock_stream_delay=0,
    )
    return TestClient(create_app(settings))


@pytest.mark.parametrize(
    "path, key",
    [
        ("/api/state", "profile"),
        ("/api/persona", "skills"),
        ("/api/fit", "rate"),
        ("/api/offers", "offers"),
        ("/api/leads", "leads"),
        ("/api/drafts", "drafts"),
        ("/api/pipeline", "columns"),
        ("/api/metrics", "headline"),
    ],
)
def test_every_read_route_serves_its_fixture(client, path, key):
    res = client.get(path)
    assert res.status_code == 200
    assert key in res.json()


def test_leads_filter_by_status(client):
    client.post("/api/leads/ld_001/verdict", json={"verdict": "useful"})
    useful = client.get("/api/leads", params={"status": "useful"}).json()["leads"]
    assert [lead["id"] for lead in useful] == ["ld_001"]


def test_verdict_updates_the_lead(client):
    res = client.post("/api/leads/ld_004/verdict", json={"verdict": "skip", "note": "Below my floor"})
    assert res.status_code == 200
    assert res.json()["status"] == "skip"
    assert res.json()["verdict_note"] == "Below my floor"


def test_invalid_verdict_uses_error_shape(client):
    res = client.post("/api/leads/ld_001/verdict", json={"verdict": "love-it"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_request"


def test_unknown_lead_is_404_with_error_shape(client):
    res = client.post("/api/leads/nope/verdict", json={"verdict": "skip"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_seed_adds_an_unfetched_lead_first(client):
    res = client.post("/api/seeds", json={"url": "https://example.com/job/42"})
    assert res.status_code == 201
    assert res.json()["fetched"] is False
    assert client.get("/api/leads").json()["leads"][0]["url"] == "https://example.com/job/42"


@pytest.mark.parametrize("bad", ["javascript:alert(document.cookie)//x", "data:text/html,hi", "not a url at all"])
def test_seed_refuses_non_http_links(client, bad):
    res = client.post("/api/seeds", json={"url": bad})
    assert res.status_code == 422
    assert all(lead["url"] != bad for lead in client.get("/api/leads").json()["leads"])


def test_persona_update_accepts_known_fields(client):
    res = client.put("/api/persona", json={"hours_per_week": 25, "income_goal_monthly": 12000})
    assert res.status_code == 200
    assert res.json()["hours_per_week"] == 25


@pytest.mark.parametrize(
    "bad",
    [{"skills": 42}, {"hours_per_week": "lots"}, {"hours_per_week": 500}, {"is_admin": True}],
)
def test_persona_update_rejects_bad_types_and_unknown_fields(client, bad):
    assert client.put("/api/persona", json=bad).status_code == 422
    assert client.get("/api/persona").json()["skills"] == ["Python", "FastAPI", "React", "LLM agents"]


def test_approve_sends_with_the_edited_text(client):
    res = client.post("/api/drafts/dr_001/approve", json={"final_text": "Edited body"})
    assert res.json()["status"] == "sent"
    assert res.json()["body"] == "Edited body"
    assert client.get("/api/drafts", params={"status": "pending"}).json()["drafts"] == []


def test_reject_keeps_the_note(client):
    res = client.post("/api/drafts/dr_001/reject", json={"note": "Too long"})
    assert res.json()["status"] == "rejected"


def test_reset_requires_token_and_restores_fixtures(client):
    client.post("/api/leads/ld_001/verdict", json={"verdict": "skip"})
    assert client.post("/api/reset").status_code == 401
    assert client.post("/api/reset", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/reset", headers={"Authorization": f"Bearer {TOKEN}"}).json() == {"ok": True}
    assert client.get("/api/leads").json()["leads"][0]["status"] == "new"


def test_chat_streams_contract_events_and_ends_with_done(client):
    with client.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "What should I charge?"}]}) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[len("data: "):]) for line in res.iter_lines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "tool"
    assert "text" in types
    assert types[-1] == "done"


def test_chat_rejects_empty_messages(client):
    assert client.post("/api/chat", json={"messages": []}).status_code == 422


def test_cv_upload_adds_a_source(client):
    res = client.post("/api/cv", files={"file": ("my-cv.pdf", b"%PDF-1.4 sample", "application/pdf")})
    assert res.status_code == 200
    assert any(s.endswith("my-cv.pdf") for s in res.json()["sources"])


def _site_client(tmp_path, with_files: bool) -> TestClient:
    dist = tmp_path / "dist"
    landing = tmp_path / "landing.html"
    if with_files:
        dist.mkdir()
        (dist / "index.html").write_text("<div id=root></div>")
        landing.write_text("<h1>Chipi landing</h1>")
    settings = Settings(
        mock_mode=True,
        data_dir=tmp_path,
        fixtures_dir=REPO_ROOT / "fixtures",
        mcp_token=TOKEN,
        cors_origins=(),
        mock_stream_delay=0,
        web_dist=dist,
        landing_file=landing,
    )
    return TestClient(create_app(settings))


def test_site_serves_landing_and_app(tmp_path):
    site = _site_client(tmp_path, with_files=True)
    assert "Chipi landing" in site.get("/").text
    assert "id=root" in site.get("/app/").text
    redirect = site.get("/app", follow_redirects=False)
    assert redirect.status_code in (301, 302, 307, 308)
    assert redirect.headers["location"].endswith("/app/")
    # API still wins over the website routes
    assert site.get("/api/state").status_code == 200


def test_site_routes_absent_when_not_built(tmp_path):
    site = _site_client(tmp_path, with_files=False)
    assert site.get("/").status_code == 404
    assert site.get("/app/").status_code == 404
