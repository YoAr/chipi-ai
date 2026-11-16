"""Scout (spec v2 §6.1): deterministic lead ingestion from Recruitee boards. No model calls.

    run_scout:     discovery terms -> site:recruitee.com searches -> company slugs ->
                   /api/offers/ (<= 1 request/second) -> normalize -> dedupe -> filter ->
                   language + skill tags -> leads with status "filtered"
    run_snapshot:  baseline panel (chosen with broad terms, independent of the freelancer's
                   skills) -> today's postings -> posting_snapshots (demand index input)

Both run as background jobs (app/jobs.py) from /api/scout and /mcp, and report counts per
step so the funnel (spec §10) starts at "discovered".
"""

from __future__ import annotations

import re
from itertools import zip_longest
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import jobs
from .config import Settings
from .providers.search import search_provider
from .sources import recruitee
from .storage import companies
from .storage.events import append_event
from .tagging import tag_skills

JOB = "scout"
SNAPSHOT_JOB = "panel_snapshot"
MAX_AGE_DAYS = 30
MAX_TERMS = 10
MAX_COMPANIES_PER_RUN = 80
REQUEST_GAP_SECONDS = 1.0     # politeness: at most one board request per second
LOCATION = "Amsterdam"
NL_TERMS = ("netherlands", "nederland", "amsterdam")
# Baseline panel: broad, skill-neutral queries (spec: chosen independently of any freelancer)
PANEL_TERMS = ("vacatures", "vacature Amsterdam", "jobs Amsterdam", "careers Amsterdam", "werken bij",
               "we're hiring", "open positions", "openstaande vacatures", "join our team", "solliciteer")
PANEL_TARGET = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def exclusions_for(store) -> list[str]:
    """The freelancer's exclusions: approved profile when it exists, else the persona's 'avoids'."""
    profile = store.get_setting("profile_exclusions")  # set on profile approval (Interviewer milestone)
    if isinstance(profile, list):
        return [str(x).strip().lower() for x in profile if str(x).strip()]
    avoids = (store.get("persona") or {}).get("avoids") or ""
    return [p.strip().lower() for p in re.split(r"[,;\n]", avoids) if len(p.strip()) >= 3]


def skills_for(store) -> list[str]:
    return [s for s in (store.get("persona") or {}).get("skills") or [] if s]


def default_terms(store) -> list[str]:
    """Approved services' discovery terms (NL + EN) when they exist, else the persona's skills."""
    terms = store.get_setting("discovery_terms")  # set on services approval (Offer designer milestone)
    if isinstance(terms, dict):
        # Interleave NL and EN so a cap of MAX_TERMS still searches both languages
        nl, en = terms.get("nl") or [], terms.get("en") or []
        merged = [t for pair in zip_longest(nl, en) for t in pair if t]
        merged = list(dict.fromkeys(merged))
        if merged:
            return merged[:MAX_TERMS]
    return skills_for(store)[:MAX_TERMS]


def filter_reason(lead: dict, exclusions: list[str], now: datetime | None = None) -> str | None:
    """Why a posting is filtered out, or None if it passes (spec §6.1 step 6)."""
    now = now or _now()
    published = lead.get("published_at")
    if not published:
        return "no publish date"
    age = now - datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if age > timedelta(days=MAX_AGE_DAYS):
        return f"older than {MAX_AGE_DAYS} days"
    country = lead.get("country_code")
    # Trust the board's country code; fall back to the location text only when it's missing
    in_nl = country == "NL" if country else any(t in (lead.get("location") or "").lower() for t in NL_TERMS)
    if not in_nl and not lead.get("remote"):
        return "not in the Netherlands and not remote"
    text = f"{lead.get('title') or ''}\n{lead.get('description') or ''}".lower()
    for word in exclusions:
        if word and word in text:
            return f"matches your exclusion '{word}'"
    return None


def _fetch_boards(store, client: httpx.Client, slugs: list[str], sleep) -> tuple[dict[str, list[dict]], list[dict]]:
    """{slug: postings} for each board, ≤ 1 request/second. Failures are recorded, not fatal."""
    out: dict[str, list[dict]] = {}
    failed: list[dict] = []
    for i, slug in enumerate(slugs):
        if i:
            sleep(REQUEST_GAP_SECONDS)
        try:
            postings = recruitee.fetch_postings(client, slug, sleep=sleep)
        except recruitee.RecruiteeError as exc:
            companies.record_fetch(store.conn, recruitee.SOURCE, slug, error=str(exc)[:300])
            failed.append({"slug": slug, "error": str(exc)[:200]})
            continue
        first = postings[0] if postings else {}
        companies.record_fetch(store.conn, recruitee.SOURCE, slug, name=first.get("company"),
                               domain=first.get("domain"), sector=first.get("sector"))
        out[slug] = postings
    return out, failed


def run_scout(store, settings: Settings, terms: list[str] | None = None, *, search=None,
              client: httpx.Client | None = None, sleep=time.sleep) -> dict:
    terms = [t.strip() for t in (terms or default_terms(store)) if t and t.strip()][:MAX_TERMS]
    if not terms:
        raise ValueError("No discovery terms yet: add skills to your profile or approve your services")
    search = search or search_provider(settings)

    jobs.step(store, JOB, "discovering", f"Searching Recruitee boards for {len(terms)} terms")
    found = recruitee.discover(search, terms, LOCATION)
    for slug, term in found.items():
        companies.upsert_company(store.conn, recruitee.SOURCE, slug, discovered_via=term)
    slugs = list(found)[:MAX_COMPANIES_PER_RUN]

    jobs.step(store, JOB, "fetching", f"Reading {len(slugs)} company job boards")
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        boards, failed = _fetch_boards(store, client, slugs, sleep)
    finally:
        if own_client:
            client.close()

    jobs.step(store, JOB, "filtering", "Filtering and tagging postings")
    exclusions, extra_skills = exclusions_for(store), skills_for(store)
    now = _now()
    discovered, kept, reasons = 0, [], {}
    for postings in boards.values():
        for lead in postings:
            discovered += 1
            reason = filter_reason(lead, exclusions, now)
            if reason:
                key = reason.split("'")[0].strip()
                reasons[key] = reasons.get(key, 0) + 1
                continue
            lead["skills"] = tag_skills(f"{lead['title']}\n{lead['description']}", extra_skills)
            if not lead["skills"]:
                # A board found through one term also lists unrelated jobs (e.g. an agency's
                # non-tech roles); without a single skill match the Researcher would only drop it.
                reasons["no matching skills"] = reasons.get("no matching skills", 0) + 1
                continue
            lead["status"] = "filtered"
            lead["why"] = f"{lead.get('department') or 'Role'} at {lead['company']}, posted {lead['published_at'][:10]}"
            kept.append(lead)
    added = store.upsert_leads(kept)

    result = {
        "terms": terms, "searches": len(terms), "companies_found": len(found), "boards_read": len(boards),
        "boards_failed": failed, "postings_discovered": discovered, "passed_filters": len(kept),
        "filtered_out": reasons, "new_leads": len(added), "duplicates": len(kept) - len(added),
        "languages": _count(added, "language"),
    }
    append_event(store.data_dir, "run_completed", "agent", "scout", result)
    jobs.done(store, JOB, f"Read {len(boards)} boards: {discovered} postings, {len(added)} new leads", result)
    return result


def run_snapshot(store, settings: Settings, *, search=None, client: httpx.Client | None = None,
                 sleep=time.sleep, grow: bool = True, max_companies: int = PANEL_TARGET) -> dict:
    """Grow the baseline panel toward PANEL_TARGET, then snapshot today's postings for it."""
    panel = companies.companies(store.conn, recruitee.SOURCE, panel_only=True)
    if grow and len(panel) < PANEL_TARGET:
        jobs.step(store, SNAPSHOT_JOB, "discovering", "Growing the baseline panel of Dutch companies")
        found = recruitee.discover(search or search_provider(settings), list(PANEL_TERMS), "Netherlands")
        for slug in found:
            companies.upsert_company(store.conn, recruitee.SOURCE, slug, discovered_via="panel", in_panel=True)
        panel = companies.companies(store.conn, recruitee.SOURCE, panel_only=True)
    slugs = [c["slug"] for c in panel][:max_companies]

    jobs.step(store, SNAPSHOT_JOB, "fetching", f"Reading {len(slugs)} panel job boards")
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        boards, failed = _fetch_boards(store, client, slugs, sleep)
    finally:
        if own_client:
            client.close()
    day = _now().strftime("%Y-%m-%d")
    rows = 0
    for slug, postings in boards.items():
        for p in postings:
            p["skills"] = tag_skills(f"{p['title']}\n{p['description']}")
        rows += companies.save_snapshot(store.conn, day, recruitee.SOURCE, slug, postings)
    result = {"day": day, "panel_size": len(panel), "boards_read": len(boards),
              "boards_failed": len(failed), "postings": rows}
    append_event(store.data_dir, "run_completed", "agent", "panel_snapshot", result)
    jobs.done(store, SNAPSHOT_JOB, f"Snapshot {day}: {rows} postings from {len(boards)} companies", result)
    return result


def _count(items: list[dict], key: str) -> dict:
    out: dict = {}
    for item in items:
        out[item.get(key)] = out.get(item.get(key), 0) + 1
    return out
