"""Writer (spec v2 §5.4, Judgment tier "smart") and Verifier (§5.5, Standard tier "check").

    run_draft(store, settings, lead_id, language=None)
      write -> code pre-checks (app/ai/prechecks.py) -> model review (fresh context, other model)
      -> revise on failures (max MAX_REVISIONS) -> draft "ready", or "needs_human" with the issues

The writer never grades itself: pre-checks are code, the review is a separate call on the
"check" model that sees only the draft and its sources. Nothing is sent: the freelancer copies
a draft and sends it themselves (spec §2.1); copying is recorded as their approval.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .. import jobs, profiles, service_catalog
from ..compliance import DISCLAIMER
from ..config import Settings
from ..providers.fetcher import PageFetcher
from . import client as ai
from . import prechecks

MAX_REVISIONS = 2
WRITER_PROMPT_VERSION = "writer-v1"
VERIFIER_PROMPT_VERSION = "verifier-v1"


def job_key(lead_id: str) -> str:
    return f"draft:{lead_id}"


# Contracts --------------------------------------------------------------------------
class WriterDraft(BaseModel):
    """Spec v2 §5.4 output contract."""
    channel: Literal["warm_intro", "apply_interim", "direct_note"]
    language: Literal["nl", "en"]
    subject: str | None
    body: str
    forwardable_blurb: str | None
    claims_used: list[str] = Field(description="The evidence claims you used, copied exactly from EVIDENCE.")
    hook_source_url: str


class Issue(BaseModel):
    severity: Literal["blocker", "major", "minor"]
    check: str
    detail: str


class Review(BaseModel):
    """Spec v2 §5.5 output contract ("pass" is stored as `pass`)."""
    passed: bool = Field(description="True only if there are no blocker or major issues.")
    issues: list[Issue]
    revision_notes: str | None


WRITER_INSTRUCTIONS = """You write one short outreach message for an independent freelancer in the Netherlands, ready to paste. Language: {language}.

Rules:
- First sentence: the HOOK, specific and attributed to the company (what they did or announced, from the brief). Use only what the hook summary says; keep names and numbers exactly as written.
- Then the solution in at most 2 sentences, specific to this company: the matched SERVICE as a scoped project with its deliverable and timeframe. The problem hypothesis is a hypothesis: write "probably"/"waarschijnlijk", never state it as fact.
- One proof point, ONLY from EVIDENCE, and list the evidence claim you used in claims_used, copied exactly. Name a client only if the evidence says it may be named.
- One low-friction ask (e.g. a short call, or "shall I send a one-page scope?"). Don't add numbers that aren't in the inputs.
- At most 120 words in body, in the freelancer's voice (see VOICE SAMPLES), in {language}. No flattery, no filler, no exclamation marks.
- Never employment framing: no "join your team", "full-time", hours per week, "under your supervision", "detachering", "inhuur", "uur per week". It is an independent project with a deliverable.
- Never invent facts, numbers, clients or results. No placeholders like [name].
- Channel {channel}:
  - apply_interim: a note for the posting's application form, offering to deliver a scoped project now while they hire. subject = null, forwardable_blurb = null.
  - warm_intro: a message to the mutual contact ({warm}) asking for an introduction, plus forwardable_blurb: 2-3 sentences they can forward. subject = null.
  - direct_note: an email with subject (at most 7 words) and a final one-line opt-out ("If this isn't relevant, just say so and I won't follow up." / "Niet relevant? Laat het weten, dan benader ik je niet meer."). forwardable_blurb = null.
- hook_source_url: copy the brief's hook source URL.
{revision}"""

REVIEW_INSTRUCTIONS = """You are the Verifier. You did not write this draft. Check it against its SOURCES and report issues. Be strict but fair.

Check:
1. Company claims: every statement about the company is supported by the SOURCES; paraphrases are accurate (blocker if not).
2. Specificity: the proposed solution is specific to this company. If it would make sense sent to any company, that is a major issue.
3. First line earns attention: specific, recent, attributed (major if generic).
4. Independence: it reads as an independent, scoped project with a deliverable, not disguised employment (blocker if it reads as joining the team, hours-based or open-ended).
5. Tone and language: matches the VOICE SAMPLES; natural {language_name} (minor or major).
6. Proof point: only from the freelancer's EVIDENCE; nothing invented (blocker if invented).
passed = true only if there are no blocker or major issues. revision_notes: concrete instructions for the writer, or null."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lang_name(lang: str) -> str:
    return "Dutch" if lang == "nl" else "English"


def _evidence(profile: dict) -> list[dict]:
    out = []
    for s in profile.get("skills", []):
        for e in s.get("evidence", []):
            out.append({"skill": s["name"], "claim": e["claim"], "metric": e.get("metric"),
                        "client": e.get("client") if e.get("citable") else None,
                        "client_may_be_named": bool(e.get("citable"))})
    for c in profile.get("skill_combinations", []):
        out.append({"skill": " + ".join(c["skills"]), "claim": c["evidence"], "metric": None, "client": None,
                    "client_may_be_named": False})
    return out


def _page_text(store, brief: dict, lead: dict) -> str:
    hook = brief.get("hook") or {}
    if hook.get("raw"):
        try:
            return (store.data_dir / hook["raw"]).read_text(encoding="utf-8")
        except OSError:
            return ""
    return f"{lead.get('title') or ''}\n{lead.get('description') or ''}"


def _writer_input(lead, brief, service, profile, language) -> str:
    return json.dumps({
        "posting": {k: lead.get(k) for k in ("title", "company", "url", "apply_url")},
        "brief": {"hook": brief.get("hook"), "problem_hypothesis": brief.get("problem_hypothesis"),
                  "channel": brief.get("channel"), "warm_connection": brief.get("warm_connection")},
        "service": {k: service.get(k) for k in ("name", "problem", "deliverable", "duration_weeks", "target_buyer")},
        "evidence": _evidence(profile),
        "voice_samples": profile.get("voice_samples", [])[:3],
        "language": language,
    }, ensure_ascii=False)


def _review_input(draft: dict, brief: dict, lead: dict, page_text: str, service: dict, profile: dict) -> str:
    return json.dumps({
        "draft": {k: draft.get(k) for k in ("channel", "language", "subject", "body", "forwardable_blurb")},
        "sources": {"hook": brief.get("hook"), "hook_page_excerpt": page_text[:4000],
                    "posting": {"title": lead.get("title"), "company": lead.get("company"),
                                "description_excerpt": (lead.get("description") or "")[:3000]}},
        "service": {k: service.get(k) for k in ("name", "deliverable", "duration_weeks")},
        "evidence": _evidence(profile),
        "voice_samples": profile.get("voice_samples", [])[:3],
    }, ensure_ascii=False)


def _write(settings, lead, brief, service, profile, language, notes: str | None) -> dict:
    revision = f"\nREVISION: fix these problems from the last draft:\n{notes}" if notes else ""
    instructions = WRITER_INSTRUCTIONS.format(language=_lang_name(language), channel=brief["channel"],
                                              warm=brief.get("warm_connection") or "the contact", revision=revision)
    out = ai.structured("smart", settings, instructions=instructions,
                        input=_writer_input(lead, brief, service, profile, language), schema=WriterDraft)
    d = out.model_dump()
    d.update({"channel": brief["channel"], "language": language})  # code decides these, not the writer
    if d["channel"] != "direct_note":
        d["subject"] = None
    if d["channel"] != "warm_intro":
        d["forwardable_blurb"] = None
    return d


def write_and_verify(store, settings: Settings, lead: dict, language: str | None = None, *,
                     fetcher=None, now=None) -> dict:
    now = now or _now()
    brief = lead.get("brief")
    if not brief or not brief.get("fit"):
        raise ValueError("Research this lead first: only leads with a fitting brief get a draft")
    profile = profiles.approved_profile(store)
    services = {s["id"]: s for s in service_catalog.approved_services(store)}
    service = services.get(brief.get("service_id"))
    if not profile or not service:
        raise ValueError("The matched service is no longer approved; research the lead again")
    language = language or brief.get("draft_language") or "en"
    floor = profiles.rate_floor(store)
    page_text = _page_text(store, brief, lead)
    fetcher = fetcher or PageFetcher(settings.oxylabs_key)
    hook_url = (brief.get("hook") or {}).get("source_url")
    hook_status = fetcher.fetch(hook_url).get("status") if hook_url else None

    notes, history, review = None, [], None
    key = job_key(lead["id"])
    for round_ in range(1, MAX_REVISIONS + 2):
        jobs.step(store, key, "writing", "Writing your message" if round_ == 1 else f"Revising (round {round_})")
        draft = _write(settings, lead, brief, service, profile, language, notes)
        checks = prechecks.run_all(draft, brief, lead, profile, service, page_text, hook_status, floor, now)
        failed = [c for c in checks if not c["passed"]]
        review = None
        if not failed:
            jobs.step(store, key, "verifying", "Checking it against the sources")
            review = ai.structured("check", settings,
                                   instructions=REVIEW_INSTRUCTIONS.format(language_name=_lang_name(language)),
                                   input=_review_input(draft, brief, lead, page_text, service, profile), schema=Review)
        serious = [i for i in (review.issues if review else []) if i.severity in ("blocker", "major")]
        passed = not failed and review is not None and review.passed and not serious
        history.append({"round": round_, "failed_checks": [c["name"] for c in failed],
                        "review_issues": [i.model_dump() for i in (review.issues if review else [])]})
        if passed or round_ > MAX_REVISIONS:
            break
        notes = "\n".join([f"- {c['name']}: {c['note']}" for c in failed] +
                          [f"- [{i.severity}] {i.check}: {i.detail}" for i in serious] +
                          ([f"- {review.revision_notes}"] if review and review.revision_notes else []))

    review_checks = []
    if review is not None:
        review_checks = [{"name": f"Reviewer: {i.check}", "passed": i.severity == "minor", "note": i.detail}
                         for i in review.issues]
        if not review.issues:
            review_checks = [{"name": "Reviewer: accurate, specific, independent framing, natural language",
                              "passed": True, "note": None}]
    verifier = {
        "passed": passed, "pass": passed, "rounds": round_, "checks": checks + review_checks,
        "issues": [i.model_dump() for i in review.issues] if review else [],
        "revision_notes": review.revision_notes if review else None, "history": history,
        "guidance": guidance(draft, brief),
        "provenance": {"writer": {"model": ai.model_for("smart", settings), "prompt_version": WRITER_PROMPT_VERSION},
                       "verifier": {"model": ai.model_for("check", settings), "prompt_version": VERIFIER_PROMPT_VERSION},
                       "input_ref": {"lead_id": lead["id"], "hook_source": hook_url, "hook_raw": (brief.get("hook") or {}).get("raw")},
                       "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
    }
    return store.save_v2_draft({
        "lead_id": lead["id"], "channel": draft["channel"], "language": language, "subject": draft["subject"],
        "body": draft["body"], "forwardable_blurb": draft["forwardable_blurb"], "claims_used": draft["claims_used"],
        "hook_source_url": hook_url, "service_id": service["id"], "round": round_, "verifier": verifier,
        "status": "ready" if passed else "needs_human", "template_version": WRITER_PROMPT_VERSION,
    })


def guidance(draft: dict, brief: dict) -> list[str]:
    """Compliance guidance shown with the draft (spec §6.5). Not legal advice."""
    out = []
    if draft.get("channel") == "direct_note":
        out.append("Dutch rules on unsolicited email and the GDPR apply. Send to a published role address "
                   "(jobs@, info@), not a person's own address, and keep the opt-out line.")
    out.append("Keep it an independent project with a defined deliverable (Wet DBA): no set hours, "
               "no working under their direction.")
    out.append(DISCLAIMER)
    return out


def run_draft(store, settings: Settings, lead_id: str, language: str | None = None) -> None:
    lead = store.lead(lead_id)
    draft = write_and_verify(store, settings, lead, language)
    msg = ("Draft ready to copy" if draft["status"] == "ready"
           else "Draft needs your review: it didn't pass every check in 3 rounds")
    jobs.done(store, job_key(lead_id), msg, {"draft_id": draft["id"], "status": draft["status"]})


def run_batch(store, settings: Settings, limit: int = 10) -> dict:
    """Draft the researched leads that have no draft yet."""
    leads = store.leads_to_draft(limit)
    counts = {"ready": 0, "needs_human": 0, "failed": 0}
    for i, lead in enumerate(leads, 1):
        jobs.step(store, "drafts", "writing", f"Drafting {i} of {len(leads)}: {lead.get('company')}")
        try:
            d = write_and_verify(store, settings, lead)
            counts[d["status"]] += 1
        except ai.AIUnavailable:
            raise
        except Exception:  # one bad lead never stops the batch
            counts["failed"] += 1
    jobs.done(store, "drafts", f"Drafted {len(leads)} leads: {counts['ready']} ready, "
                                f"{counts['needs_human']} need your review", {**counts, "leads": len(leads)})
    return counts
