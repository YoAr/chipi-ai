"""Plain-code guards for the product rules (rate floor, approval, allowlist)."""
from __future__ import annotations

import hmac
import re
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


# Money as written in text: "€120/hour", "EUR 120", "120 euro", "$95", "€6.000", "€120k".
_NUM = r"\d[\d.,]*\d|\d"
_MONEY_RE = re.compile(
    rf"(?P<pre>€|\$|£|\b(?:EUR|USD|GBP)\b)\s?(?P<n1>{_NUM})\s*(?P<k1>k\b)?"
    rf"|(?P<n2>{_NUM})\s*(?P<k2>k)?\s?(?P<post>€|\b(?:EUR|euros?|USD|dollars?|GBP)\b)",
    re.IGNORECASE,
)


def _currency(mark: str) -> str:
    m = mark.lower()
    if m in ("€", "eur", "euro", "euros"):
        return "EUR"
    if m in ("£", "gbp"):
        return "GBP"
    return "USD"


def parse_amount(token: str) -> float | None:
    """A figure as written: "120", "1,200", "1.200" (Dutch thousands), "97,50", "6,000.50".

    With both separators the last one is the decimal point; a lone separator followed by groups
    of exactly three digits is a thousands separator, otherwise a decimal point.
    """
    t = (token or "").strip()
    if not t or not t[0].isdigit():
        return None
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(",", ".")
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        parts = t.split(sep)
        if len(parts) > 1 and 1 <= len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
            t = "".join(parts)
        elif len(parts) == 2:
            t = f"{parts[0]}.{parts[1]}"
        else:
            return None
    try:
        return float(t)
    except ValueError:
        return None


def money_amounts(text: str) -> list[tuple[str, float]]:
    """[(currency "EUR"|"USD"|"GBP", amount)] for every amount of money written in `text`."""
    out = []
    for m in _MONEY_RE.finditer(text or ""):
        num, k, mark = (m.group("n1"), m.group("k1"), m.group("pre")) if m.group("pre") else \
            (m.group("n2"), m.group("k2"), m.group("post"))
        value = parse_amount(num)
        if value is not None:
            out.append((_currency(mark), value * 1000 if k else value))
    return out


def fmt_eur(n: float) -> str:
    """€120 / €97.5: how a EUR amount is shown to the user."""
    n = float(n)
    return f"€{int(n)}" if n.is_integer() else f"€{n:g}"


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
