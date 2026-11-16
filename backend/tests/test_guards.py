import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.guards import (
    GuardError, RateLimiter, check_rate, check_recipient, check_send_allowed,
    is_http_url, parse_rate_floor, require_token,
)

GOAL = """# Goal
<!-- FIXED CORE -->
## Limits
- Rate floor: $100/hour. Never quote below it.
<!-- ───── DERIVED: rewritten ───── -->
- Rate floor: $5/hour (bogus)
"""


def test_parse_floor():
    assert parse_rate_floor(GOAL) == 100


def test_parse_floor_missing_or_only_derived():
    with pytest.raises(GuardError):
        parse_rate_floor("# nothing")
    with pytest.raises(GuardError):
        parse_rate_floor("<!-- DERIVED -->\n- Rate floor: $5/hour")


def test_check_rate():
    check_rate(100, 100)
    check_rate(150.5, 100)
    with pytest.raises(GuardError):
        check_rate(99.99, 100)


def test_send_allowed():
    check_send_allowed({"status": "approved", "approved_at": "2026-01-01T00:00:00Z"})
    for d in ({"status": "pending"}, {"status": "approved"},
              {"status": "approved", "approved_at": ""}, {}):
        with pytest.raises(GuardError):
            check_send_allowed(d)


def test_recipient():
    check_recipient("Me@Example.com", ["me@example.com"])
    with pytest.raises(GuardError):
        check_recipient("x@example.com", ["me@example.com"])
    with pytest.raises(GuardError):
        check_recipient("me@example.com", [])
    with pytest.raises(GuardError):
        check_recipient("me@example.com.evil.io", ["me@example.com"])


def test_is_http_url():
    assert is_http_url("https://a.com/x")
    assert is_http_url("http://a.com")
    for u in ("javascript:alert(1)", "ftp://a.com", "https://", "a.com", "", "file:///etc/passwd"):
        assert not is_http_url(u)


def _client(expected):
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(require_token(expected))])
    def x():
        return {"ok": True}

    return TestClient(app)


def test_require_token():
    c = _client("s3cret")
    assert c.get("/x", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/x", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/x").status_code == 401


def test_require_token_empty_expected_always_401():
    c = _client("")
    assert c.get("/x").status_code == 401
    assert c.get("/x", headers={"Authorization": "Bearer "}).status_code == 401


def test_rate_limiter():
    t = [0.0]
    rl = RateLimiter(2, 10, clock=lambda: t[0])
    assert rl.check("a") and rl.check("a")
    assert not rl.check("a")
    assert rl.check("b")
    t[0] = 10.0
    assert rl.check("a")


def test_rate_floor_reads_the_real_goal_file():
    # The fixed-core comment also says "the DERIVED line"; the split must use the marker itself.
    from app.config import REPO_ROOT
    from app.guards import parse_rate_floor

    assert parse_rate_floor((REPO_ROOT / "data" / "goal.md").read_text()) > 0
