"""Plain-code guards for the product rules (rate floor, approval, allowlist)."""
from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque
from typing import Callable
from urllib.parse import urlparse

from fastapi import Header, HTTPException


class GuardError(Exception):
    """A product rule would be broken."""


def check_rate(amount: float, floor: float | None) -> None:
    """Rule 2: never below the floor. The floor comes from the approved profile
    (app.profiles.rate_floor); with no approved profile there is no floor, so nothing may be priced."""
    if floor is None:
        raise GuardError("No approved rate floor yet: approve your profile before anything is priced")
    if amount < floor:
        raise GuardError(f"Rate €{amount} is below your floor of €{floor}/hour")


def check_send_allowed(draft: dict) -> None:
    if draft.get("status") != "approved" or not draft.get("approved_at"):
        raise GuardError("Draft is not approved; nothing is sent without approval")


def check_recipient(addr: str, allowlist: list[str]) -> None:
    allowed = {a.strip().lower() for a in allowlist if a and a.strip()}
    if not allowed:
        raise GuardError("Email allowlist is empty; nothing may be sent")
    if (addr or "").strip().lower() not in allowed:
        raise GuardError(f"Recipient {addr!r} is not on the allowlist")


def is_http_url(url: str) -> bool:
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.hostname)


def require_token(expected: str) -> Callable[..., None]:
    def dependency(authorization: str = Header(default="")) -> None:
        if not expected or not hmac.compare_digest(
            authorization.encode(), f"Bearer {expected}".encode()
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    return dependency


class RateLimiter:
    def __init__(
        self,
        max_calls: int,
        per_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._clock = clock
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            q = self._calls[key]
            while q and now - q[0] >= self.per_seconds:
                q.popleft()
            if len(q) >= self.max_calls:
                return False
            q.append(now)
            return True


def client_ip(scope) -> str:
    """The visitor's address, for rate limits.

    On Fly every request reaches the app through Fly's proxy, so the socket address is
    the proxy's and would put all visitors in one bucket. Fly sets `Fly-Client-IP` to
    the real address, and the app is only reachable through that proxy. Locally there
    is no such header and the socket address is used.
    Accepts an ASGI scope or a Starlette Request (anything with `.scope`).
    """
    scope = getattr(scope, "scope", scope)
    for name, value in scope.get("headers") or []:
        if name == b"fly-client-ip" and value:
            return value.decode("latin-1").strip()
    client = scope.get("client")
    return client[0] if client else "unknown"
