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

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings
from .guards import RateLimiter

HEADER = b"x-demo-passcode"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT = {"/api/reset", "/api/passcode/check"}
NEEDED = {"error": {"code": "passcode_required", "message": "This demo needs a passcode to make changes."}}
TOO_MANY = {"error": {"code": "rate_limited", "message": "Too many wrong passcodes. Wait a minute and try again."}}


def _matches(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


class PasscodeMiddleware:
    """Pure ASGI, so streamed responses (chat) pass through untouched."""

    def __init__(self, app, passcode: str, limiter: RateLimiter):
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
            if not _matches(given, self.passcode):
                client = (scope.get("client") or ("unknown", 0))[0]
                if given and not self.limiter.check(client):
                    return await _reply(send, 429, TOO_MANY)
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
    limiter = RateLimiter(max_calls=10, per_seconds=60)  # wrong guesses per client
    app.add_middleware(PasscodeMiddleware, passcode=settings.demo_passcode, limiter=limiter)
    router = APIRouter()

    @router.get("/api/passcode")
    def passcode_info() -> dict:
        return {"required": bool(settings.demo_passcode)}

    @router.post("/api/passcode/check")
    def passcode_check(body: CheckIn, request: Request):
        if not settings.demo_passcode or _matches(body.passcode, settings.demo_passcode):
            return {"ok": True}
        client = request.client.host if request.client else "unknown"
        if not limiter.check(client):
            return JSONResponse(status_code=429, content=TOO_MANY)
        return JSONResponse(status_code=401, content=NEEDED)

    app.include_router(router)
