"""Verifier step 1: code pre-checks (spec v2 §5.5, §6.4). Plain functions, no model call, fail fast.

Each check returns {"name", "passed", "note"}; `run_all` runs them in order. Failures go back to
the Writer listed by name. The sources a draft may rely on are: the hook's fetched page (saved in
raw/) or the posting, the approved profile's evidence, and the matched service.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..compliance import find_banned

MAX_WORDS = 120
MAX_SUBJECT_WORDS = 7
HOOK_MAX_AGE_DAYS = 30
_PLACEHOLDER = re.compile(r"\[[^\]]{1,60}\]|\{[^}]{1,60}\}|<[A-Z_ ]{2,40}>|\bX{3,}\b|\bTODO\b|\bTBD\b|lorem ipsum", re.I)
_OPT_OUT = re.compile(
    r"(opt[- ]?out|unsubscribe|not interested|not relevant|isn'?t relevant|no longer hear|won'?t follow up|"
    r"will not follow up|let me know if you'?d rather not|reply ['\"]?stop|afmelden|geen interesse|niet relevant|"
    r"niet meer (?:ontvangen|benaderd|benaderen)|benader ik (?:je|u) niet meer|laat (?:het )?(?:me )?weten als je dit liever niet)",
    re.I)
_MONTHS_DAYS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "januari", "februari", "maart", "mei", "juni", "juli", "augustus", "oktober",
    "monday", "tuesday", "wednesday", "thursday", "friday", "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag",
}
_EUR = re.compile(r"(?:€\s?|eur\s?|euro\s)(\d{2,4})(?:[.,]\d+)?|(\d{2,4})\s?(?:€|euro|eur)\b", re.I)


def _check(name: str, passed: bool, note: str | None = None) -> dict:
    return {"name": name, "passed": bool(passed), "note": None if passed else note}


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-zà-ÿ]+", "", (text or "").lower())


def numbers(text: str) -> set[str]:
    """Numbers as written, normalised (1.200 / 1,200 -> 1200; 4,2 -> 4.2)."""
    out = set()
    for n in re.findall(r"\d+(?:[.,]\d+)*", text or ""):
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", n):
            n = re.sub(r"[.,]", "", n)
        out.add(n.replace(",", "."))
    return out


def _date_words(iso: str | None) -> str:
    """The hook date as numbers a sentence might use: 2026-11-09 -> "2026 11 9 09"."""
    if not iso:
        return ""
    y, m, d = iso[:10].split("-")
    return f"{y} {int(m)} {m} {int(d)} {d}"


def first_sentence(text: str) -> str:
    body = (text or "").strip()
    body = re.sub(r"^(hi|hello|hey|dear|beste|hoi|hallo|goedemiddag|goedemorgen)\b[^\n,]*[,\n]\s*", "", body, flags=re.I)
    m = re.search(r"(.+?[.!?])(\s|$)", body, re.S)
    return (m.group(1) if m else body).strip()


def hook_resolves(status: int | None) -> dict:
    return _check("Hook source link works", status is not None and 200 <= status < 400,
                  f"the hook source returned {status or 'no response'}")


def hook_recent(published_at: str | None, now: datetime) -> dict:
    ok = False
    if published_at:
        d = datetime.strptime(published_at[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        ok = now - timedelta(days=HOOK_MAX_AGE_DAYS) <= d <= now + timedelta(days=1)
    return _check(f"Hook is from the last {HOOK_MAX_AGE_DAYS} days", ok, f"hook dated {published_at or 'unknown'}")


def hook_facts_on_page(body: str, page_text: str, company: str) -> dict:
    """Numbers and capitalised names in the opening sentence must appear on the hook's source page."""
    opening = first_sentence(body)
    missing = sorted(numbers(opening) - numbers(page_text))
    page = _squash(page_text) + _squash(company)
    skip = {"Hi", "Hello", "Dear", "Beste", "Hoi", "Hallo", "The", "De", "Het", "Een", "Your", "Uw", "Jullie",
            "Je", "Congratulations", "Gefeliciteerd", "Saw", "Zag", "Last", "This", "Deze", "Dit", "Vorige"}
    skip |= {m.capitalize() for m in _MONTHS_DAYS}
    words = re.findall(r"[\w&'’-]+", opening)[1:]  # the first word is capitalised anyway
    names, run = [], []
    for w in words + [""]:
        compound = re.search(r"-[a-zà-ÿ]", w)  # Dutch compounds like "AI-platformimplementaties" aren't names
        if len(w) >= 3 and w[0].isupper() and w not in skip and not compound:
            run.append(w)
        elif run:
            names.append(" ".join(run))
            run = []
    missing += [n for n in names if _squash(n) not in page]
    return _check("Hook facts are on the source page", not missing, "not on the page: " + ", ".join(missing))


def length(body: str, subject: str | None, channel: str) -> list[dict]:
    out = [_check(f"At most {MAX_WORDS} words", _words(body) <= MAX_WORDS, f"{_words(body)} words")]
    if channel == "direct_note":
        n = _words(subject or "")
        out.append(_check(f"Subject of 1-{MAX_SUBJECT_WORDS} words", 1 <= n <= MAX_SUBJECT_WORDS, f"subject has {n} words"))
    return out


def no_placeholders(*texts: str | None) -> dict:
    found = [m.group(0) for t in texts for m in _PLACEHOLDER.finditer(t or "")]
    return _check("No unfilled placeholders", not found, "found: " + ", ".join(found[:5]))


def no_banned(*texts: str | None) -> dict:
    found = find_banned(" ".join(t or "" for t in texts))
    return _check("Independent project framing (no employment phrases)", not found, "; ".join(found))


def claims_in_evidence(claims_used: list[str], profile: dict) -> dict:
    evidence = [_squash(e.get("claim", "")) + _squash(e.get("metric") or "") + _squash(e.get("client") or "")
                for s in profile.get("skills", []) for e in s.get("evidence", [])]
    combos = [_squash(c.get("evidence", "")) for c in profile.get("skill_combinations", [])]
    pool = evidence + combos
    missing = [c for c in claims_used if not any(_squash(c) and (_squash(c) in p or p in _squash(c)) for p in pool if p)]
    return _check("Every proof point is in your profile evidence", claims_used is not None and not missing,
                  "not in your evidence: " + "; ".join(missing))


def numbers_supported(body: str, subject: str | None, allowed_text: str) -> dict:
    missing = sorted(numbers(f"{subject or ''} {body}") - numbers(allowed_text))
    return _check("Every number comes from a source", not missing, "unsupported numbers: " + ", ".join(missing))


def rate_not_below_floor(body: str, floor: float | None) -> dict:
    amounts = [int(a or b) for a, b in _EUR.findall(body or "")]
    low = [a for a in amounts if floor is not None and a < floor]
    return _check("No rate below your floor", not low, f"€{low[0]} is below your floor €{floor:g}" if low else None)


def opt_out(body: str, channel: str) -> dict | None:
    if channel != "direct_note":
        return None
    return _check("Direct note has an opt-out line", bool(_OPT_OUT.search(body or "")), "add a one-line opt-out")


def run_all(draft: dict, brief: dict, lead: dict, profile: dict, service: dict, page_text: str,
            hook_status: int | None, floor: float | None, now: datetime | None = None,
            kind: str = "initial") -> list[dict]:
    """kind="followup": the message doesn't open with the hook, so the hook checks are skipped."""
    now = now or datetime.now(timezone.utc)
    body, subject, channel = draft.get("body") or "", draft.get("subject"), draft.get("channel") or brief["channel"]
    hook = brief.get("hook") or {}
    evidence_text = " ".join(f"{e.get('claim')} {e.get('metric') or ''} {e.get('client') or ''}"
                             for s in profile.get("skills", []) for e in s.get("evidence", []))
    def g(x):  # 8.0 -> "8", so "8 weken" matches a duration stored as a float
        return f"{x:g}" if isinstance(x, (int, float)) else str(x or "")

    rr = service["rate_range_eur"]
    service_text = (f"{service.get('deliverable')} {g(service.get('duration_weeks'))} weeks "
                    f"{g(rr['min'])} {g(rr['max'])} {service.get('name')}")
    page_text = f"{page_text} {_date_words(hook.get('published_at'))}"  # "on 9 November" may cite the page's own date
    allowed = f"{page_text} {lead.get('description') or ''} {lead.get('title') or ''} {evidence_text} {service_text}"
    hook_checks = [] if kind == "followup" else [
        hook_resolves(hook_status),
        hook_recent(hook.get("published_at"), now),
        hook_facts_on_page(body, page_text, lead.get("company") or ""),
    ]
    checks = [
        *hook_checks,
        *length(body, subject, channel),
        no_placeholders(body, subject, draft.get("forwardable_blurb")),
        no_banned(body, subject, draft.get("forwardable_blurb")),
        claims_in_evidence(draft.get("claims_used") or [], profile),
        numbers_supported(body, subject, allowed),
        rate_not_below_floor(body, floor),
    ]
    if (o := opt_out(body, channel)) is not None:
        checks.append(o)
    return checks
