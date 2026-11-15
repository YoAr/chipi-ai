"""Demo passcode: off unless DEMO_PASSCODE is set; when set, every change needs it."""

import pytest
from fastapi.testclient import TestClient

from app.config import REPO_ROOT, Settings
from app.main import create_app

CODE = "open-sesame"
TOKEN = "reset-token"
SEED = {"url": "https://example.com/jobs/42"}


def make(tmp_path, passcode=""):
    s = Settings(mock_mode=True, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token=TOKEN,
                 cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                 demo_passcode=passcode)
    return TestClient(create_app(s))


def test_off_by_default(tmp_path):
    with make(tmp_path) as c:
        assert c.get("/api/passcode").json() == {"required": False}
        assert c.post("/api/seeds", json=SEED).status_code == 201


@pytest.fixture()
def locked(tmp_path):
    with make(tmp_path, CODE) as c:
        yield c


def test_reading_stays_open(locked):
    assert locked.get("/api/passcode").json() == {"required": True}
    for path in ("/api/state", "/api/leads", "/api/drafts", "/api/metrics", "/health"):
        assert locked.get(path).status_code == 200


@pytest.mark.parametrize("method, path, body", [
    ("post", "/api/seeds", SEED),
    ("post", "/api/leads/ld_001/verdict", {"verdict": "skip"}),
    ("post", "/api/drafts/dr_001/approve", {"final_text": "x"}),
    ("put", "/api/persona", {"hours_per_week": 20}),
    ("post", "/api/chat", {"messages": [{"role": "user", "content": "hi"}]}),
])
def test_changes_need_the_passcode(locked, method, path, body):
    res = getattr(locked, method)(path, json=body)
    assert res.status_code == 401 and res.json()["error"]["code"] == "passcode_required"
    wrong = getattr(locked, method)(path, json=body, headers={"X-Demo-Passcode": "nope"})
    assert wrong.status_code == 401
    right = getattr(locked, method)(path, json=body, headers={"X-Demo-Passcode": CODE})
    assert right.status_code in (200, 201)


def test_upload_needs_the_passcode(locked):
    files = {"file": ("cv.txt", b"Sam. Python, 5 years.", "text/plain")}
    assert locked.post("/api/cv", files=files).status_code == 401
    assert locked.post("/api/cv", files=files, headers={"X-Demo-Passcode": CODE}).status_code == 200


def test_reset_keeps_its_own_token(locked):
    assert locked.post("/api/reset", headers={"Authorization": f"Bearer {TOKEN}"}).json() == {"ok": True}


def test_check_endpoint(locked):
    assert locked.post("/api/passcode/check", json={"passcode": CODE}).json() == {"ok": True}
    assert locked.post("/api/passcode/check", json={"passcode": "nope"}).status_code == 401


def test_wrong_guesses_are_rate_limited(locked):
    codes = [locked.post("/api/passcode/check", json={"passcode": f"guess{i}"}).status_code for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_blocked_guesser_cannot_tell_right_from_wrong(locked):
    for i in range(10):
        locked.post("/api/passcode/check", json={"passcode": f"guess{i}"})
    # Over the limit: even the RIGHT passcode is refused, on both the check and a real change
    assert locked.post("/api/passcode/check", json={"passcode": CODE}).status_code == 429
    assert locked.post("/api/seeds", json=SEED, headers={"X-Demo-Passcode": CODE}).status_code == 429


def test_wrong_guesses_on_changes_count_too(locked):
    for i in range(10):
        assert locked.post("/api/seeds", json=SEED, headers={"X-Demo-Passcode": f"g{i}"}).status_code == 401
    assert locked.post("/api/seeds", json=SEED, headers={"X-Demo-Passcode": CODE}).status_code == 429


def test_visitors_behind_fly_proxy_get_separate_limits(locked):
    attacker = {"Fly-Client-IP": "203.0.113.9"}
    for i in range(10):
        locked.post("/api/passcode/check", json={"passcode": f"guess{i}"}, headers=attacker)
    assert locked.post("/api/passcode/check", json={"passcode": CODE}, headers=attacker).status_code == 429
    judge = {"Fly-Client-IP": "198.51.100.7"}  # same proxy, different visitor: not blocked
    assert locked.post("/api/passcode/check", json={"passcode": CODE}, headers=judge).json() == {"ok": True}


def test_chat_limit_is_per_visitor(tmp_path):
    with make(tmp_path) as c:
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        for _ in range(20):
            c.post("/api/chat", json=payload, headers={"Fly-Client-IP": "203.0.113.9"})
        assert c.post("/api/chat", json=payload, headers={"Fly-Client-IP": "203.0.113.9"}).status_code == 429
        assert c.post("/api/chat", json=payload, headers={"Fly-Client-IP": "198.51.100.7"}).status_code == 200
