"""Demo passcode: on the public deploy, changing anything needs a passcode.

Switched on only when DEMO_PASSCODE is set (a Fly secret). Locally it is unset, so
nothing changes. When on:
- Every POST/PUT/PATCH/DELETE under /api/ needs the header `X-Demo-Passcode`.
  That covers uploads, verdicts, approvals, AI actions and chat (which costs money).
- Reading (GET) stays open, so anyone with the link can look around.
- /api/reset keeps its own token; /mcp and /hooks are outside /api/ and use their own checks.
- Wrong guesses are rate-limited per client.

Wire it with one call in create_app():  install(app, settings)
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings
from .guards import client_ip

HEADER = b"x-demo-passcode"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT = {"/api/reset", "/api/passcode/check"}
NEEDED = {"error": {"code": "passcode_required", "message": "This demo needs a passcode to make changes."}}
TOO_MANY = {"error": {"code": "rate_limited", "message": "Too many wrong passcodes. Wait a minute and try again."}}


def _matches(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


class FailureLimiter:
    """Counts WRONG guesses per client. Once over the limit, the client is refused
    outright (right guesses included) until the window passes, so a blocked
    guesser can't tell a right passcode from a wrong one."""

    def __init__(self, max_failures: int = 10, per_seconds: float = 60, clock=time.monotonic):
        self.max, self.window, self.clock = max_failures, per_seconds, clock
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str) -> deque:
        q, cutoff = self._fails[key], self.clock() - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._trim(key)) >= self.max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._trim(key).append(self.clock())


class PasscodeMiddleware:
    """Pure ASGI, so streamed responses (chat) pass through untouched."""

    def __init__(self, app, passcode: str, limiter: FailureLimiter):
        self.app, self.passcode, self.limiter = app, passcode, limiter

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and self.passcode
            and scope["method"] in WRITE_METHODS
            and scope["path"].startswith("/api/")
            and scope["path"] not in EXEMPT
        ):
            given = dict(scope.get("headers") or []).get(HEADER, b"").decode("latin-1")
            ip = client_ip(scope)
            if given and self.limiter.blocked(ip):  # check the limit BEFORE comparing
                return await _reply(send, 429, TOO_MANY)
            if not _matches(given, self.passcode):
                if given:
                    self.limiter.record_failure(ip)
                return await _reply(send, 401, NEEDED)
        await self.app(scope, receive, send)


async def _reply(send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})


class CheckIn(BaseModel):
    passcode: str


def install(app: FastAPI, settings: Settings) -> None:
    limiter = FailureLimiter(max_failures=10, per_seconds=60)  # wrong guesses per visitor
    app.add_middleware(PasscodeMiddleware, passcode=settings.demo_passcode, limiter=limiter)
    router = APIRouter()

    @router.get("/api/passcode")
    def passcode_info() -> dict:
        return {"required": bool(settings.demo_passcode)}

    @router.post("/api/passcode/check")
    def passcode_check(body: CheckIn, request: Request):
        if not settings.demo_passcode:
            return {"ok": True}
        ip = client_ip(request)
        if limiter.blocked(ip):  # check the limit BEFORE comparing
            return JSONResponse(status_code=429, content=TOO_MANY)
        if _matches(body.passcode, settings.demo_passcode):
            return {"ok": True}
        limiter.record_failure(ip)
        return JSONResponse(status_code=401, content=NEEDED)

    app.include_router(router)
