"""Recruitee public job boards: JobSource (spec v2 §6.1, §7).

    discover(search, terms, location) -> [company_slug]   via site:recruitee.com searches
    fetch_postings(client, slug)       -> [lead dict]       GET https://{slug}.recruitee.com/api/offers/

Verified against live responses 2026-11-15 (no auth). Fields used per offer: id, title,
department, city, country_code, location, remote, hybrid, employment_type_code,
published_at ("YYYY-MM-DD HH:MM:SS UTC"), description, requirements, careers_url,
careers_apply_url, company_name, category_code, translations (keys = posting languages).
`mailbox_email` is deliberately not stored: it can be a person's address (spec §2.6, §6.5).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from ..tagging import detect_language
from .normalize import new_lead, strip_html

SOURCE = "recruitee"
HEADERS = {"User-Agent": "chipi-ai/0.1 (job lead finder; polite, <=1 req/s)"}
_SLUG_URL = re.compile(r"^https?://([a-z0-9][a-z0-9-]{0,62})\.recruitee\.com(?:/|$)", re.I)
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# Recruitee's own subdomains, never a company board
_NOT_COMPANIES = {"www", "app", "api", "blog", "help", "support", "status", "docs", "careers",
                  "developers", "partners", "marketplace", "academy", "go", "info"}


class RecruiteeError(Exception):
    """A board couldn't be read (gone, blocked, or not JSON)."""


def slug_from_url(url: str) -> str | None:
    m = _SLUG_URL.match((url or "").strip())
    if not m:
        return None
    slug = m.group(1).lower()
    return None if slug in _NOT_COMPANIES else slug


def discovery_query(term: str, location: str) -> str:
    return f'site:recruitee.com "{term.strip()}" {location}'.strip()


def discover(search, terms: list[str], location: str = "Amsterdam", per_term: int = 20) -> dict[str, str]:
    """{slug: term that found it}. One search per term; results about people are never kept."""
    found: dict[str, str] = {}
    for term in terms:
        if not (term or "").strip():
            continue
        for result in search.search(discovery_query(term, location), count=per_term, locale="NL"):
            slug = slug_from_url(result.get("url", ""))
            if slug and slug not in found:
                found[slug] = term
    return found


def _iso(ts: str | None) -> str | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(ts.replace("Z", "+0000") if "T" in ts else ts, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def parse_offer(offer: dict, slug: str) -> dict | None:
    if not isinstance(offer, dict) or not offer.get("id") or not offer.get("title"):
        return None
    if offer.get("status") not in (None, "published"):
        return None
    description = strip_html(f"{offer.get('description') or ''}\n{offer.get('requirements') or ''}", limit=8000)
    langs = list((offer.get("translations") or {}).keys())
    hint = langs[0] if len(langs) == 1 else None
    remote = offer.get("remote")
    careers_url = offer.get("careers_url") or f"https://{slug}.recruitee.com/o/{offer.get('slug') or offer['id']}"
    lead = new_lead(
        title=offer["title"],
        company=offer.get("company_name") or slug,
        source=SOURCE,
        url=careers_url,
        fetched=bool(description),
    )
    posting_id = f"{SOURCE}:{offer['id']}"
    lead.update({
        "dedup_key": posting_id,
        "source_posting_id": posting_id,
        "company_slug": slug,
        "department": offer.get("department"),
        "city": offer.get("city"),
        "country_code": (offer.get("country_code") or "").upper() or None,
        "location": offer.get("location") or offer.get("city"),
        "remote": None if remote is None else bool(remote),
        "hybrid": bool(offer.get("hybrid")),
        "employment_type": offer.get("employment_type_code"),
        "language": detect_language(f"{offer['title']}\n{description}", hint),
        "published_at": _iso(offer.get("published_at") or offer.get("created_at")),
        "description": description,
        "apply_url": offer.get("careers_apply_url"),
        "sector": offer.get("category_code"),
        "domain": urlsplit(careers_url).hostname,
    })
    return lead


def fetch_postings(client: httpx.Client, slug: str, retries: int = 2, sleep=time.sleep) -> list[dict]:
    """All published offers on one board. Backs off on 429/5xx; raises RecruiteeError otherwise."""
    if not _SLUG_OK.match(slug or ""):
        raise RecruiteeError(f"Bad Recruitee slug {slug!r}")
    url = f"https://{slug}.recruitee.com/api/offers/"
    for attempt in range(retries + 1):
        try:
            r = client.get(url, headers=HEADERS, timeout=20)
        except httpx.HTTPError as exc:
            if attempt == retries:
                raise RecruiteeError(f"{slug}: {type(exc).__name__}") from exc
            sleep(2 ** (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                raise RecruiteeError(f"{slug}: HTTP {r.status_code}")
            sleep(2 ** (attempt + 1))
            continue
        if r.status_code != 200:
            raise RecruiteeError(f"{slug}: HTTP {r.status_code}")
        try:
            offers = r.json().get("offers") or []
        except ValueError as exc:
            raise RecruiteeError(f"{slug}: not JSON") from exc
        return [lead for lead in (parse_offer(o, slug) for o in offers) if lead]
    return []
