"""Route plumbing for the AI actions: 202 → status, mock-mode block, rate limit, readable job errors.

These don't test the AI itself (see docs/ai-agents.md). They monkeypatch the runner
functions, so they keep passing as the partner fills in app/ai/*.
"""

import pytest
from fastapi.testclient import TestClient

from app.ai import drafts, profile, scoring
from app.config import REPO_ROOT, Settings
from app.main import create_app


def settings(tmp_path, mock=False):
    return Settings(mock_mode=mock, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    openai_key_set=True, model_smart="m", model_check="m", model_fast="m")


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as c:
        yield c


ACTIONS = [
    ("/api/profile/build", "/api/profile/status"),
    ("/api/search", "/api/search/status"),
    ("/api/leads/ld_x/draft", "/api/leads/ld_x/draft/status"),
]


def test_an_unbuilt_piece_fails_with_a_readable_error(client, monkeypatch):
    # All three pieces are built now; the "Not built yet" mapping stays for any new stub.
    def unbuilt(store, s):
        raise NotImplementedError("build_fit (app/ai/profile.py)")

    monkeypatch.setattr(profile, "run_profile", unbuilt)
    assert client.post("/api/profile/build").status_code == 202
    job = client.get("/api/profile/status").json()
    assert job["status"] == "error" and job["error"].startswith("Not built yet")


@pytest.mark.parametrize("start, status", ACTIONS)
def test_blocked_in_sample_data_mode(tmp_path, start, status):
    with TestClient(create_app(settings(tmp_path, mock=True))) as c:
        assert c.post(start).status_code == 409
        assert c.get(status).status_code == 409


def test_runner_progress_reaches_status(client, monkeypatch):
    def fake_profile(store, s):
        from app import jobs
        jobs.step(store, "profile", "fit", "Working out your rate")
        jobs.done(store, "profile", "Your profile, rate and offers are ready")

    monkeypatch.setattr(profile, "run_profile", fake_profile)
    assert client.post("/api/profile/build").json()["status"] == "running"
    assert client.get("/api/profile/status").json()["message"] == "Your profile, rate and offers are ready"


def test_search_passes_keywords(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(scoring, "run_search", lambda store, s, kw: seen.setdefault("kw", kw))
    client.post("/api/search", json={"keywords": ["fastapi", "llm"]})
    assert seen["kw"] == ["fastapi", "llm"]
    assert client.get("/api/search/status").json()["status"] == "done"


def test_draft_result_carries_draft_id(client, monkeypatch):
    from app import jobs
    monkeypatch.setattr(drafts, "run_draft",
                        lambda store, s, lead_id, language=None: jobs.done(store, drafts.job_key(lead_id), "Draft ready",
                                                                           {"draft_id": "dr_1"}))
    client.post("/api/leads/ld_9/draft")
    assert client.get("/api/leads/ld_9/draft/status").json()["result"] == {"draft_id": "dr_1"}


def test_actions_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(scoring, "run_search", lambda *a: None)
    codes = [client.post("/api/search").status_code for _ in range(11)]
    assert codes[:10] == [202] * 10 and codes[10] == 429
