"""Outreach drafts and the verifier.

Spec: docs/ai-agents.md → "Drafts & verifier". The route (app/routes/drafts.py) is
done: POST /api/leads/{id}/draft runs `run_draft` in the background; the UI polls
GET /api/leads/{id}/draft/status (result.draft_id when done).

Rules (CLAUDE.md): never invent experience; never below the rate floor; the writer
never grades itself (the model check is a separate call on the "check" model).
Save with store.add_draft({...}); shape: fixtures/drafts.json.

Flow: write (smart) → code checks → one rewrite if a check the writer can fix failed →
model check (check model, fresh context) → save, even when checks fail (the user decides).
Channel and recipient are decided in code from the post, never by the model.

Money is EUR per hour, excl. VAT. The floor is the approved profile's
(app.profiles.rate_floor); with no approved profile nothing is priced, so no draft is written.
"""

from __future__ import annotations

import json
import os
import re

from pydantic import BaseModel

from .. import guards, jobs, profiles
from ..config import Settings
from . import client as ai

MAX_WORDS = 150
CV_CHARS = 12000  # enough of the CV for the checker; keeps the call small

# Check names: unique (the UI keys on them), user-facing.
RATE_MATCHES = "Rate matches your ask"
NOT_BELOW_FLOOR = "Not below your floor"
LENGTH_ONE_ASK = "Under 150 words, one ask"
CHANNEL_ALLOWED = "Channel allowed"
CLAIMS_BACKED = "Claims backed by your CV"
PROFESSIONAL_TONE = "Professional tone"
# The writer controls the text, not the channel or recipient, so only these are worth a rewrite.
REWRITABLE = {RATE_MATCHES, NOT_BELOW_FLOOR, LENGTH_ONE_ASK}
NO_FLOOR = "Approve your profile first: it sets your minimum hourly rate."

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WORD_RE = re.compile(r"\S+")


def job_key(lead_id: str) -> str:
    return f"draft:{lead_id}"


# Model output shapes ---------------------------------------------------------


class DraftText(BaseModel):
    subject: str
    body: str


class DraftReview(BaseModel):
    claims_backed: bool
    unsupported_claims: list[str]
    claims_note: str
    professional_tone: bool
    tone_note: str


# Helpers ---------------------------------------------------------------------


def _post_text(lead: dict) -> str:
    parts = [lead.get(k) for k in ("title", "company", "rate_text", "description")]
    return "\n".join(str(p) for p in parts if p)


def pick_channel(lead: dict) -> tuple[str, str]:
    """("email", address) if the post contains an email address, else ("apply_link", lead url)."""
    m = _EMAIL_RE.search(_post_text(lead))
    if m:
        return "email", m.group(0).rstrip(".")
    return "apply_link", lead.get("url") or ""


def _ask(fit: dict | None) -> float | None:
    rate = (fit or {}).get("rate") or {}
    return rate.get("ask")


def _money(text: str) -> list[tuple[str, float]]:
    """[(currency, amount)] for every amount of money in the text; anything but EUR is wrong here."""
    return guards.money_amounts(text)


def _show(cur: str, amount: float) -> str:
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(cur, f"{cur} ")
    return guards.fmt_eur(amount).replace("€", sym, 1)


_eur = guards.fmt_eur


def _allowlist(settings: Settings) -> list[str]:
    raw = getattr(settings, "email_allowlist", None) or os.getenv("EMAIL_ALLOWLIST", "")
    if isinstance(raw, str):
        raw = raw.split(",")
    return [a.strip() for a in raw if a and a.strip()]


def _floor(store, fit: dict | None) -> float:
    """EUR/hour floor from the approved profile; the fit's floor only if there is none and the fit
    has one. Neither: nothing may be priced, so no draft (ValueError, shown to the user)."""
    floor = profiles.rate_floor(store)
    if floor is None:
        floor = ((fit or {}).get("rate") or {}).get("floor")
    if floor is None:
        raise ValueError(NO_FLOOR)
    return float(floor)


def _cv_text(store, settings: Settings) -> str:
    path = store.get_setting("cv_text_path")
    if not path:
        return ""
    try:
        return (settings.data_dir / path).read_text(encoding="utf-8", errors="replace")[:CV_CHARS]
    except OSError:
        return ""


def _find_lead(store, lead_id: str) -> dict:
    for lead in store.leads()["leads"]:
        if lead["id"] == lead_id:
            if hasattr(store, "lead_post"):  # the post text, so an email address in it is found
                lead = {**lead, "description": store.lead_post(lead_id)}
            return lead
    raise ValueError(f"No lead with id {lead_id}")


def _persona_facts(persona: dict) -> dict:
    keys = ("name", "role", "years", "location", "skills", "wants", "hours_per_week")
    return {k: persona.get(k) for k in keys if persona.get(k) not in (None, "", [])}


# The four steps ---------------------------------------------------------------

WRITER_INSTRUCTIONS = """You write a short first outreach message from a freelancer to a client about one job post.

Rules, all required:
- Under 150 words in the body. Plain text, no markdown.
- Exactly one clear ask: a short call (for example a 20-minute call this week). It is the only question in the body.
- Quote the hourly rate exactly as given: {rate} (euros, excluding VAT). Mention no other amount of
  money and never use dollars.
- Mention only experience, skills, years and facts that appear in the freelancer's profile below.
  Never invent projects, clients, results, numbers or availability.
- Where a fact would help but is not in the profile, write a bracketed placeholder in capitals,
  for example [ONE RELEVANT PROJECT], [HOURS], [NAME]. The freelancer fills these in.
- Greet the reader as [NAME] unless the post gives a contact name. Sign with the freelancer's
  first name if the profile has one, otherwise [YOUR NAME].
- Professional, warm and direct. No flattery, no exclamation marks.
- Subject: under 10 words, specific to the post."""


def write_draft(lead: dict, persona: dict, fit: dict, settings: Settings, feedback: list[str] | None = None) -> dict:
    """Return {subject, body, channel, to}. Under 150 words, one ask, quotes the ask rate."""
    ask = _ask(fit)
    if not fit or ask is None:
        raise ValueError("Build your profile first")
    if ((fit.get("rate") or {}).get("currency") or "EUR") != "EUR":
        raise ValueError("Your rate was worked out in another currency; rebuild your profile to get a rate in EUR")
    rate = f"{_eur(ask)}/hour"
    payload = {
        "job_post": {k: lead.get(k) for k in ("title", "company", "url", "rate_text", "description") if lead.get(k)},
        "freelancer_profile": _persona_facts(persona or {}),
        "fit": [{"name": f.get("name"), "reason": f.get("reason")} for f in (fit.get("fits") or [])],
        "rate_to_quote": rate,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if feedback:
        text += ("\n\nA previous draft failed these checks. Write a new draft that passes them:\n"
                 + "\n".join(f"- {f}" for f in feedback))
    out = ai.structured("smart", settings, instructions=WRITER_INSTRUCTIONS.format(rate=rate),
                        input=text, schema=DraftText)
    channel, to = pick_channel(lead)
    return {"subject": out.subject.strip(), "body": out.body.strip(), "channel": channel, "to": to}


def code_checks(draft: dict, lead: dict, persona: dict, fit: dict, floor: float | None,
                allowlist: list[str] | None = None) -> list[dict]:
    """[{name, passed, note}]: rate matches ask, not below floor, ≤150 words/one ask, channel allowed.

    `allowlist` is the email allowlist (EMAIL_ALLOWLIST); run_draft passes it in.
    """
    text = f"{draft.get('subject') or ''}\n{draft.get('body') or ''}"
    ask = _ask(fit)
    quoted = _money(text)
    checks = []

    # Rate matches your ask: the ask appears in euros, and no other amount (in any currency) does.
    if ask is None:
        checks.append({"name": RATE_MATCHES, "passed": False, "note": "No ask rate yet"})
    elif not quoted:
        checks.append({"name": RATE_MATCHES, "passed": False, "note": f"Doesn't quote your ask of {_eur(ask)}/hour"})
    else:
        wrong = sorted({(c, q) for c, q in quoted if c != "EUR" or q != float(ask)})
        foreign = any(c != "EUR" for c, _ in wrong)
        checks.append({
            "name": RATE_MATCHES, "passed": not wrong,
            "note": "" if not wrong else
            f"Quotes {', '.join(_show(c, q) for c, q in wrong)}; your ask is {_eur(ask)}/hour"
            + (" (quote in euros)" if foreign else ""),
        })

    # Not below your floor (EUR): the ask and every euro amount quoted. Other currencies fail above.
    if floor is None:
        checks.append({"name": NOT_BELOW_FLOOR, "passed": False,
                       "note": "No approved rate floor yet: approve your profile first"})
    else:
        below = []
        for amount in ([ask] if ask is not None else []) + [q for c, q in quoted if c == "EUR"]:
            try:
                guards.check_rate(amount, floor)
            except guards.GuardError:
                below.append(amount)
        checks.append({
            "name": NOT_BELOW_FLOOR, "passed": not below,
            "note": "" if not below else
            f"{', '.join(_eur(b) for b in sorted(set(below)))} is below your floor of {_eur(floor)}/hour",
        })

    # Under 150 words, one ask (one question in the body).
    body = draft.get("body") or ""
    words = len(_WORD_RE.findall(body))
    asks = body.count("?")
    problems = []
    if words > MAX_WORDS:
        problems.append(f"{words} words (max {MAX_WORDS})")
    if asks != 1:
        problems.append(f"{asks} questions; needs exactly one ask")
    checks.append({"name": LENGTH_ONE_ASK, "passed": not problems, "note": "; ".join(problems)})

    # Channel allowed: email only to the allowlist; an apply link must be a real web link.
    channel, to = draft.get("channel"), draft.get("to") or ""
    if channel == "email":
        try:
            guards.check_recipient(to, allowlist or [])
            checks.append({"name": CHANNEL_ALLOWED, "passed": True, "note": ""})
        except guards.GuardError as exc:
            checks.append({"name": CHANNEL_ALLOWED, "passed": False, "note": str(exc)})
    elif channel == "apply_link":
        ok = guards.is_http_url(to)
        checks.append({"name": CHANNEL_ALLOWED, "passed": ok,
                       "note": "" if ok else "The post has no web link to apply at"})
    else:
        checks.append({"name": CHANNEL_ALLOWED, "passed": False, "note": f"Unknown channel {channel!r}"})
    return checks


CHECKER_INSTRUCTIONS = """You review an outreach message a freelancer is about to send. You did not write it.

1. Claims: list every claim the message makes about the freelancer (skills, years, projects,
   clients, results, location, availability). Each must be supported by the CV text or the profile.
   Bracketed placeholders like [ONE RELEVANT PROJECT] are not claims; ignore them. Rates are checked
   elsewhere; ignore them. claims_backed is true only if every claim is supported; put any unsupported
   claim, quoted briefly, in unsupported_claims.
2. Tone: professional_tone is true if the message is polite, direct and businesslike, with no
   flattery, pressure, slang or exaggeration.
Notes are one short sentence each, written to the freelancer; leave a note empty when the check passes."""


def model_check(draft: dict, lead: dict, persona: dict, settings: Settings, cv_text: str = "") -> list[dict]:
    """Separate 'check' model call, fresh context: claims backed by the CV; professional tone."""
    payload = {
        "message": {"subject": draft.get("subject"), "body": draft.get("body")},
        "job_post_title": lead.get("title"),
        "profile": _persona_facts(persona or {}),
        "cv_text": cv_text or "(no CV text available; judge against the profile only)",
    }
    try:
        review = ai.structured("check", settings, instructions=CHECKER_INSTRUCTIONS,
                               input=json.dumps(payload, ensure_ascii=False, indent=1), schema=DraftReview)
    except ai.AIUnavailable as exc:
        # The draft itself is fine to keep; show the user this check didn't run rather than lose the draft.
        note = f"Couldn't run this check: {exc}"[:200]
        return [{"name": CLAIMS_BACKED, "passed": False, "note": note},
                {"name": PROFESSIONAL_TONE, "passed": False, "note": note}]
    claims_note = review.claims_note.strip()
    if not review.claims_backed and review.unsupported_claims:
        listed = "Not in your CV: " + "; ".join(review.unsupported_claims)
        claims_note = f"{claims_note} ({listed})" if claims_note else listed
    return [
        {"name": CLAIMS_BACKED, "passed": bool(review.claims_backed),
         "note": "" if review.claims_backed else claims_note},
        {"name": PROFESSIONAL_TONE, "passed": bool(review.professional_tone),
         "note": "" if review.professional_tone else review.tone_note.strip()},
    ]


def run_draft(store, settings: Settings, lead_id: str) -> None:
    key = job_key(lead_id)
    lead = _find_lead(store, lead_id)
    persona, fit = store.get("persona"), store.get("fit")
    floor = _floor(store, fit)
    allowlist = _allowlist(settings)

    jobs.step(store, key, "writing", "Writing your message")
    draft = write_draft(lead, persona, fit, settings)
    jobs.step(store, key, "checking", "Checking rate, length and channel")
    checks = code_checks(draft, lead, persona, fit, floor, allowlist)

    failed = [c for c in checks if not c["passed"] and c["name"] in REWRITABLE]
    if failed:
        jobs.step(store, key, "rewriting", "Fixing what the checks found")
        feedback = [f"{c['name']}: {c['note']}" if c["note"] else c["name"] for c in failed]
        draft = write_draft(lead, persona, fit, settings, feedback=feedback)
        checks = code_checks(draft, lead, persona, fit, floor, allowlist)

    jobs.step(store, key, "verifying", "Checking claims against your CV")
    checks += model_check(draft, lead, persona, settings, cv_text=_cv_text(store, settings))
    passed = all(c["passed"] for c in checks)

    saved = store.add_draft({
        "lead_id": lead_id, "nickname": lead.get("nickname"),
        "channel": draft["channel"], "to": draft["to"],
        "subject": draft["subject"], "body": draft["body"],
        "verifier": {"passed": passed, "checks": checks},
    })
    message = "Draft ready" if passed else "Draft ready; some checks failed, review before approving"
    jobs.done(store, key, message, {"draft_id": saved["id"]})
