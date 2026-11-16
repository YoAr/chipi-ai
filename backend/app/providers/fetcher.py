"""PageFetcher (spec v2 §7): fetch(url) -> {url, status, title, text, published_at, via}.

Plain HTTP first (httpx). When a page blocks plain requests (401/403/429/503 or a bot
challenge) and an Oxylabs key is set, the Oxylabs Web API scrape endpoint fetches it instead:

    POST https://webapi.oxylabs.io/v1/scrape   Bearer key   {"url": ..., "output": ["html"]}
    -> {"status": 200, "results": [{"html": "...", "metadata": {"url": ...}}]}

The publish date is read from the page in code, never from a model: article/og meta tags,
JSON-LD datePublished, <time datetime>, or a /YYYY/MM/DD/ path. No date found -> None.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from ..guards import is_http_url
from ..sources.normalize import strip_html

SCRAPE_URL = "https://webapi.oxylabs.io/v1/scrape"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; chipi-ai/0.1; +company research)",
           "Accept": "text/html,application/xhtml+xml", "Accept-Language": "nl,en;q=0.8"}
MAX_BYTES = 2_000_000
TEXT_LIMIT = 20_000
BLOCKED = {401, 403, 429, 503}

_META_DATE = re.compile(
    r'<meta[^>]+(?:property|name|itemprop)\s*=\s*["\'](?:article:published_time|og:published_time|'
    r'datePublished|pubdate|publish[-_]?date|date|dc\.date\.issued|parsely-pub-date|sailthru\.date)["\']'
    r'[^>]*content\s*=\s*["\']([^"\']+)["\']', re.I)
_META_DATE_REV = re.compile(
    r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)\s*=\s*["\']'
    r'(?:article:published_time|og:published_time|datePublished|pubdate|publish[-_]?date|date)["\']', re.I)
_LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
_TIME = re.compile(r'<time[^>]+datetime\s*=\s*["\']([^"\']+)["\']', re.I)
_URL_DATE = re.compile(r"/(20\d\d)[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])(?:/|-|$)")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def _to_date(value: str) -> str | None:
    v = (value or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", v)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _ld_dates(obj) -> list[str]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "datePublished" and isinstance(v, str):
                out.append(v)
            else:
                out += _ld_dates(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _ld_dates(v)
    return out


def published_date(html: str, url: str = "") -> str | None:
    """YYYY-MM-DD the page says it was published, or None."""
    for rx in (_META_DATE, _META_DATE_REV):
        for m in rx.finditer(html or ""):
            if d := _to_date(m.group(1)):
                return d
    for block in _LD.findall(html or ""):
        try:
            for v in _ld_dates(json.loads(block)):
                if d := _to_date(v):
                    return d
        except ValueError:
            continue
    m = _TIME.search(html or "")
    if m and (d := _to_date(m.group(1))):
        return d
    m = _URL_DATE.search(urlsplit(url).path if url else "")
    if m:
        return _to_date(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return None


def page_from_html(html: str, url: str, status: int, via: str) -> dict:
    title = _TITLE.search(html or "")
    body = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", html or "")
    return {"url": url, "status": status, "via": via,
            "title": strip_html(title.group(1), limit=300) if title else "",
            "text": strip_html(body, limit=TEXT_LIMIT), "published_at": published_date(html, url)}


def is_public_url(url: str) -> bool:
    """http(s) to a public host: no localhost, private, loopback or link-local addresses.
    URLs come from search results, so the server must not be steered to internal endpoints."""
    import ipaddress
    import socket

    if not is_http_url(url):
        return False
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost",) or host.endswith((".localhost", ".internal", ".local")):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _looks_blocked(r: httpx.Response) -> bool:
    if r.status_code in BLOCKED:
        return True
    head = r.text[:3000].lower() if r.status_code == 200 else ""
    return any(s in head for s in ("cf-challenge", "just a moment...", "captcha", "are you a robot"))


class PageFetcher:
    def __init__(self, oxylabs_key: str = "", client: httpx.Client | None = None, url_check=is_public_url) -> None:
        self._key = oxylabs_key
        self._client = client or httpx.Client(timeout=20, follow_redirects=True, headers=HEADERS)
        self._url_ok = url_check
        self.scrape_calls = 0

    def fetch(self, url: str) -> dict:
        if not self._url_ok(url):
            return {"url": url, "status": 0, "via": "none", "title": "", "text": "", "published_at": None,
                    "error": "not a public http(s) URL"}
        try:
            r = self._get_checked(url)
            if r is None:
                return {"url": url, "status": 0, "via": "http", "title": "", "text": "", "published_at": None,
                        "error": "redirected to a non-public address"}
            if not _looks_blocked(r):
                ctype = r.headers.get("content-type", "")
                if r.status_code == 200 and "html" in ctype and len(r.content) <= MAX_BYTES:
                    return page_from_html(r.text, str(r.url), 200, "http")
                return {"url": str(r.url), "status": r.status_code, "via": "http", "title": "", "text": "",
                        "published_at": None, "error": f"HTTP {r.status_code} {ctype[:40]}"}
        except httpx.HTTPError as exc:
            if not self._key:
                return {"url": url, "status": 0, "via": "http", "title": "", "text": "", "published_at": None,
                        "error": type(exc).__name__}
        return self._scrape(url)

    def _get_checked(self, url: str, hops: int = 4) -> httpx.Response | None:
        """GET, following redirects by hand so every hop is checked as a public URL."""
        for _ in range(hops + 1):
            r = self._client.get(url, follow_redirects=False)
            if r.is_redirect and r.headers.get("location"):
                url = str(r.url.join(r.headers["location"]))
                if not self._url_ok(url):
                    return None
                continue
            return r
        return r

    def _scrape(self, url: str) -> dict:
        if not self._key:
            return {"url": url, "status": 403, "via": "http", "title": "", "text": "", "published_at": None,
                    "error": "blocked, and no Oxylabs key for a fallback"}
        self.scrape_calls += 1
        try:
            r = self._client.post(SCRAPE_URL, json={"url": url, "output": ["html"]},
                                  headers={"Authorization": f"Bearer {self._key}"}, timeout=150)
            data = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            return {"url": url, "status": 0, "via": "oxylabs", "title": "", "text": "", "published_at": None,
                    "error": type(exc).__name__}
        result = (data.get("results") or [{}])[0]
        html = result.get("html") or ""
        if not html:
            return {"url": url, "status": r.status_code, "via": "oxylabs", "title": "", "text": "",
                    "published_at": None, "error": f"Oxylabs scrape returned no page (HTTP {r.status_code})"}
        return page_from_html(html, (result.get("metadata") or {}).get("url") or url, 200, "oxylabs")
