from __future__ import annotations

import html
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlsplit

_STOP = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "at", "with", "we", "are"}


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _clean_url(url: str) -> str:
    u = (url or "").strip()
    try:
        p = urlsplit(u)
    except ValueError:
        return _squash(u)
    return _squash(f"{p.netloc}{p.path.rstrip('/')}")


def dedup_key(company: str, title: str, url: str) -> str:
    return f"{_squash(company)}|{_squash(title)}|{_clean_url(url)}"


def make_nickname(title: str, taken: set[str]) -> str:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    keep = [w for w in words if w not in _STOP] or words or ["lead"]
    base = "-".join(keep[:3])
    name, n = base, 2
    while name in taken:
        name = f"{base}-{n}"
        n += 1
    return name


def strip_html(text: str, limit: int = 4000) -> str:
    t = re.sub(r"(?i)<\s*(br|/p|/li|/div)\s*/?>", "\n", text or "")
    t = re.sub(r"(?i)<p\s*>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    return t[:limit]


def matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    low = (text or "").lower()
    return any(k.lower() in low for k in keywords if k)


def new_lead(**fields) -> dict:
    lead = {
        "id": "ld_" + secrets.token_hex(4),
        "nickname": "",
        "title": "",
        "company": "",
        "source": "",
        "url": "",
        "fetched": False,
        "rate_text": None,
        "score": None,
        "why": "",
        "status": "new",
        "found_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    lead.update(fields)
    lead["dedup_key"] = dedup_key(lead["company"], lead["title"], lead["url"])
    return lead
