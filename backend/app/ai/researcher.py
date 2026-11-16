"""Researcher (spec v2 §5.3) · Standard tier ("check").

For each Scout lead: decide fit and build the brief (a recent, dated hook with its source, a
problem hypothesis, the matched service and the channel), or drop the lead with a reason.

Code does the fact work; the model only judges and writes:
- Search (Oxylabs) and fetch (PageFetcher) run in code. Pages about people, social media and
  job boards are skipped; only pages that name the company and carry a publish date within
  30 days become candidate sources. The posting itself is always a source (it is a fetched job
  post with its own date), so "they're hiring for X" is a valid fallback hook.
- The model picks a hook only from those sources. Code checks the hook URL is one of them, takes
  the date from the page (never from the model), requires it within 30 days, and requires every
  number in the hook summary to appear on the page.
- Channel, warm connection and draft language are decided in code (spec §5.3 rule 5).
- One retry with the failed checks; still failing -> the lead stays "filtered" with the error.
The hook source page text is saved under raw/ so the Verifier can check facts against it later.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from .. import jobs, profiles, service_catalog
from ..config import Settings
from ..providers.fetcher import PageFetcher
from ..providers.search import search_provider
from ..storage.wiki import save_raw
from . import client as ai

JOB = "research"
PROMPT_VERSION = "researcher-v1"
HOOK_MAX_AGE_DAYS = 30
SEARCH_RESULTS = 8
MAX_FETCHES = 5
EXCERPT = 2500
TEXT_SCAN = 6000      # the company must be named in the first part of a page
SKIP_HOSTS = ("linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com",
              "youtube.com", "glassdoor.", "indeed.", "recruitee.com", "werkzoeken.nl", "nationalevacaturebank.nl",
              "monsterboard.nl", "jobbird.com", "wikipedia.org", "kvk.nl", "openkvk.nl", "companyinfo.nl")
HOOK_TYPES = ("news", "blog", "launch", "funding", "leadership", "partnership", "posting", "other")


class Hook(BaseModel):
    summary: str
    source_url: str
    published_at: str
    type: Literal["news", "blog", "launch", "funding", "leadership", "partnership", "posting", "other"]


class Source(BaseModel):
    url: str
    title: str
    published_at: str | None


class Brief(BaseModel):
    """Spec v2 §5.3 contract, plus hook.type (for reply rates by hook type, spec §10)."""
    fit: bool
    fit_reason: str
    service_id: str | None
    hook: Hook | None
    problem_hypothesis: str
    channel: Literal["warm_intro", "apply_interim", "direct_note"]
    warm_connection: str | None
    draft_language: Literal["nl", "en"]
    sources: list[Source]


INSTRUCTIONS = """You research one job posting for an independent freelancer in Amsterdam and decide whether one of their approved services fits.

You get: the POSTING, the freelancer's SERVICES, a few PROFILE facts, and CANDIDATE SOURCES (pages already fetched for you; each has an id, url, title, publish date and an excerpt). Work only from these. Never use outside knowledge about the company.

Decide:
- fit: the premise is that a company hiring for a role may buy a clearly scoped, independent project that covers part of that need NOW, while they hire. fit=true when one service could deliver a concrete, bounded piece of the work this role is being hired for (the same discipline and skills), even if the posting doesn't name the exact problem; the problem_hypothesis is where you say what that piece probably is. fit=false when the role is a different discipline than every service, is purely ongoing operational or hands-on trade work with no project-shaped part, is an internship or graduation project, or is work the freelancer excluded. Give a short, specific fit_reason either way.
- service_id: the id of the one best service.
- hook: ONE recent, specific company event from the candidate sources (news, blog post, launch, funding, leadership change, partnership). Copy source_url and published_at exactly from that source. If no source has a usable event, use the posting itself (type "posting") and summarise what they are hiring for. The summary must only state what the source says; keep names and numbers exactly as written.
- problem_hypothesis: one or two sentences linking the posting and the hook to the chosen service. Mark it as a hypothesis ("likely", "probably"), not a fact.
- sources: every source you relied on (url, title, published_at copied from the candidates).
- channel, warm_connection, draft_language: fill in your best guess; code sets them."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-zà-ÿ]+", "", (text or "").lower())


def _company_key(name: str) -> str:
    key = _squash(re.sub(r"\b(b\.?v\.?|n\.?v\.?|bv|nv|gmbh|ltd|inc|holding|group|nederland|netherlands)\b", "",
                         (name or "").lower()))
    return key


def warm_connection(company: str, profile: dict) -> str | None:
    """A past employer, school or client whose name matches the company (spec: warm_intro)."""
    target = _company_key(company)
    if len(target) < 3:
        return None
    names = list(profile.get("past_employers", [])) + list(profile.get("schools", []))
    names += [e["client"] for s in profile.get("skills", []) for e in s.get("evidence", []) if e.get("client")]
    for name in names:
        key = _company_key(name)
        if len(key) >= 3 and (key == target or key in target or target in key):
            return name
    return None


_LISTING = re.compile(r"/(tags?|category|categorie|categories|topics?|onderwerp|auteur|author)/", re.I)
_INDEX_LEAF = {"news", "nieuws", "blog", "blogs", "newsroom", "pers", "press", "actueel", "insights", "updates"}


def _skip(url: str, lead_hosts: set[str] = frozenset()) -> bool:
    """People, social media, job boards, the posting's own site, and listing/index pages (whose date
    belongs to some other article) are never candidate sources."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host or any(h in host for h in SKIP_HOSTS) or host in lead_hosts:
        return True
    path = parts.path.rstrip("/")
    leaf = path.rsplit("/", 1)[-1].lower() if path else ""
    return bool(_LISTING.search(parts.path)) or not path or leaf in _INDEX_LEAF


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", text or "")}


def within(date: str | None, days: int, now: datetime) -> bool:
    if not date:
        return False
    d = datetime.strptime(date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return now - timedelta(days=days) <= d <= now + timedelta(days=1)


def gather_sources(lead: dict, search, fetcher, now: datetime | None = None) -> tuple[list[dict], dict]:
    """Candidate sources for one lead: recent company pages + the posting. Returns (sources, cost)."""
    now = now or _now()
    company = lead.get("company") or ""
    key = _company_key(company)
    year = now.year
    lead_hosts = {(urlsplit(u).hostname or "").lower() for u in (lead.get("url"), lead.get("apply_url")) if u}
    seen, results = set(), []
    for q in (f'"{company}" nieuws {year}', f'"{company}" news {year}'):
        for r in search.search(q, count=SEARCH_RESULTS, locale="NL"):
            u = r.get("url", "")
            if u and u not in seen and not _skip(u, lead_hosts):
                seen.add(u)
                results.append(r)
    # Prefer results that name the company in the title or snippet
    results.sort(key=lambda r: key not in _squash(f"{r.get('title')} {r.get('snippet')}"))
    sources, fetched = [], 0
    for r in results[:MAX_FETCHES]:
        page = fetcher.fetch(r["url"])
        fetched += 1
        if page.get("error") or not page.get("text"):
            continue
        if key and key not in _squash(page["text"][:TEXT_SCAN]):
            continue
        if not within(page.get("published_at"), HOOK_MAX_AGE_DAYS, now):
            continue
        sources.append({"id": f"S{len(sources) + 1}", "url": page["url"], "title": page["title"] or r.get("title", ""),
                        "published_at": page["published_at"], "text": page["text"], "via": page["via"]})
    posting_date = (lead.get("published_at") or "")[:10] or None
    sources.append({"id": "P", "url": lead.get("url"), "title": f"Job posting: {lead.get('title')}",
                    "published_at": posting_date, "text": lead.get("description") or "", "via": "posting"})
    return sources, {"searches": 2, "fetches": fetched}



def brief_problems(brief: Brief, sources: list[dict], service_ids: set[str], now: datetime) -> list[str]:
    problems = []
    if brief.fit:
        if brief.service_id not in service_ids:
            problems.append(f"service_id must be one of: {', '.join(sorted(service_ids))}")
        if brief.hook is None:
            problems.append("a fitting lead needs a hook")
        else:
            src = next((s for s in sources if s["url"] == brief.hook.source_url), None)
            if src is None:
                problems.append("hook.source_url must be copied exactly from one candidate source")
            else:
                if not within(src["published_at"], HOOK_MAX_AGE_DAYS, now):
                    problems.append(f"the hook source is not dated within {HOOK_MAX_AGE_DAYS} days")
                missing = _numbers(brief.hook.summary) - _numbers(src["text"])
                if missing:
                    problems.append(f"hook summary has numbers not on the source page: {', '.join(sorted(missing))}")
        if not brief.problem_hypothesis.strip():
            problems.append("problem_hypothesis is empty")
    elif not brief.fit_reason.strip():
        problems.append("a dropped lead needs a fit_reason")
    return problems


def _inputs(lead: dict, services: list[dict], profile: dict, sources: list[dict]) -> str:
    return json.dumps({
        "posting": {k: lead.get(k) for k in ("title", "company", "department", "location", "employment_type",
                                             "language", "published_at", "url")}
                   | {"description": (lead.get("description") or "")[:3000]},
        "services": [{k: s[k] for k in ("id", "name", "target_buyer", "problem", "deliverable", "skills_used")}
                     for s in services],
        "profile": {k: profile.get(k) for k in ("industries", "preferred_work", "exclusions")},
        "candidate_sources": [{"id": s["id"], "url": s["url"], "title": s["title"], "published_at": s["published_at"],
                               "excerpt": s["text"][:EXCERPT]} for s in sources],
    }, ensure_ascii=False)


def research_lead(store, settings: Settings, lead: dict, *, search=None, fetcher=None, now=None) -> dict:
    """Research one lead. Returns {"status": researched|dropped|failed, ...}; saves the brief."""
    now = now or _now()
    from .. import tracker

    profile = profiles.approved_profile(store)
    services = service_catalog.approved_services(store)
    if not profile or not services:
        raise ValueError("Approve your profile and services first")
    if tracker.is_stopped(store, lead):
        raise ValueError(f"{lead.get('company')} said they're not interested")
    search = search or search_provider(settings)
    fetcher = fetcher or PageFetcher(settings.oxylabs_key)
    sources, cost = gather_sources(lead, search, fetcher, now)
    service_ids = {s["id"] for s in services}

    base = _inputs(lead, services, profile, sources)
    brief = ai.structured("check", settings, instructions=INSTRUCTIONS, input=base, schema=Brief)
    calls = 1
    problems = brief_problems(brief, sources, service_ids, now)
    if problems:
        brief = ai.structured("check", settings, instructions=INSTRUCTIONS,
                              input=base + "\n\nYOUR LAST BRIEF FAILED THESE CHECKS; FIX THEM: " + "; ".join(problems),
                              schema=Brief)
        calls += 1
        problems = brief_problems(brief, sources, service_ids, now)
    cost |= {"model_calls": calls, "scrapes": getattr(fetcher, "scrape_calls", 0)}
    if problems:
        store.save_research_error(lead["id"], "; ".join(problems), cost)
        return {"status": "failed", "lead_id": lead["id"], "problems": problems}

    out = brief.model_dump()
    by_url = {s["url"]: s for s in sources}
    # Code decides these (spec §5.3 rule 5); dates always come from the fetched page
    warm = warm_connection(lead.get("company") or "", profile)
    out["warm_connection"] = warm
    out["channel"] = "warm_intro" if warm else ("direct_note" if lead.get("role_email") else "apply_interim")
    out["draft_language"] = lead.get("language") if lead.get("language") in ("nl", "en") else brief.draft_language
    if brief.fit and brief.hook:
        src = by_url[brief.hook.source_url]
        out["hook"]["published_at"] = src["published_at"]
        if src["via"] == "posting":
            out["hook"]["type"] = "posting"
        else:
            out["hook"]["raw"] = save_raw(settings.data_dir, f"source-{urlsplit(src['url']).hostname}.txt",
                                          f"URL: {src['url']}\nTitle: {src['title']}\nPublished: {src['published_at']}\n"
                                          f"Fetched: {now:%Y-%m-%d}\n\n{src['text']}".encode())
        used = {brief.hook.source_url} | {s.url for s in brief.sources if s.url in by_url}
    else:
        used = {s.url for s in brief.sources if s.url in by_url}
    out["sources"] = [{"url": u, "title": by_url[u]["title"], "published_at": by_url[u]["published_at"]}
                      for u in used]
    if not brief.fit:
        out.update({"service_id": None, "hook": None})
    out["provenance"] = {"model": ai.model_for("check", settings), "prompt_version": PROMPT_VERSION,
                         "input_ref": {"lead_id": lead["id"], "sources": [s["url"] for s in sources]},
                         "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cost": cost}
    status = "researched" if brief.fit else "dropped"
    store.save_brief(lead["id"], out, status)
    return {"status": status, "lead_id": lead["id"], "brief": out}


def run_research(store, settings: Settings, limit: int = 10, lead_ids: list[str] | None = None, *,
                 search=None, fetcher=None) -> dict:
    """Research the "filtered" leads most relevant to the approved services (or the given ones)."""
    if not profiles.approved_profile(store) or not service_catalog.approved_services(store):
        raise ValueError("Approve your profile and services first")
    leads = ([store.lead(i) for i in lead_ids] if lead_ids
             else store.leads_to_research(limit, service_catalog.approved_services(store)))
    search = search or search_provider(settings)
    fetcher = fetcher or PageFetcher(settings.oxylabs_key)
    counts = {"researched": 0, "dropped": 0, "failed": 0}
    for i, lead in enumerate(leads, 1):
        jobs.step(store, JOB, "researching", f"Researching {i} of {len(leads)}: {lead.get('company')}")
        try:
            res = research_lead(store, settings, lead, search=search, fetcher=fetcher)
        except ai.AIUnavailable:
            raise
        except Exception as exc:  # one bad lead never stops the batch
            store.save_research_error(lead["id"], f"{type(exc).__name__}: {exc}"[:300], {})
            res = {"status": "failed"}
        counts[res["status"]] += 1
    result = {**counts, "leads": len(leads), "searches": getattr(search, "calls", None),
              "scrapes": getattr(fetcher, "scrape_calls", 0)}
    jobs.done(store, JOB, f"Researched {len(leads)} leads: {counts['researched']} fit, {counts['dropped']} dropped"
              + (f", {counts['failed']} failed" if counts["failed"] else ""), result)
    return result
