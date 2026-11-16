"""Interviewer (spec v2 §5.1) · Judgment tier ("smart").

Turns a resume into an evidence-backed profile through a short conversation:

    start(store, settings, language, segment)  -> first question
    answer(store, settings, text)               -> next question, or done
    run_extract(store, settings)                -> background job: transcript + resume -> Profile draft

Rules enforced in code, not only in the prompt:
- One question per turn; a question tied to a resume line must quote a line that is in the resume.
- At most MAX_QUESTIONS questions, then the interview ends.
- Profile grounding: employers, schools, clients, numbers, the rate floor, availability and
  voice samples must appear in the resume or the freelancer's answers. Anything that doesn't is
  removed (and listed in the checks), never kept. Skills above "claimed" need evidence, or they
  are lowered to "claimed".
- Structured outputs: retry once with the failures as feedback (spec §2.2).

Every draft carries provenance {model, prompt_version, input_ref, ts}.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .. import jobs
from ..config import Settings
from ..storage import versions
from ..storage.wiki import save_raw
from . import client as ai

JOB = "interview"
STATE_KEY = "interview"
MAX_QUESTIONS = 15
TURN_PROMPT_VERSION = "interviewer-turn-v1"
PROFILE_PROMPT_VERSION = "interviewer-profile-v1"
SEGMENT_DEFAULT = "tech and data freelancer"
TOPICS = ("outcomes", "clients", "skill_combinations", "preferred_work", "exclusions", "rate_floor",
          "availability", "voice_samples", "other")
# Topics that are about the freelancer's plans, not a resume line
FREE_TOPICS = {"preferred_work", "exclusions", "rate_floor", "availability", "voice_samples"}


# Output contracts ------------------------------------------------------------
class InterviewTurn(BaseModel):
    done: bool = Field(description="True when every field is covered; then question is null.")
    reflection: str = Field(description="One short sentence reflecting what was notable in the last answer; '' on the first turn.")
    question: str | None = Field(description="Exactly one question, short, in the interview language.")
    resume_line: str | None = Field(description="The resume line the question is about, copied exactly; null for preferred work, exclusions, rate, availability or writing samples.")
    topic: Literal["outcomes", "clients", "skill_combinations", "preferred_work", "exclusions", "rate_floor",
                   "availability", "voice_samples", "other"]
    questions_left: int = Field(description="Honest estimate of questions still needed, 0-15.")


class Evidence(BaseModel):
    claim: str
    metric: str | None
    client: str | None
    citable: bool


class Skill(BaseModel):
    name: str
    taxonomy_id: str | None
    evidence: list[Evidence]
    proof_strength: Literal["claimed", "demonstrated", "referenceable"]


class SkillCombination(BaseModel):
    skills: list[str]
    evidence: str


class Profile(BaseModel):
    """Spec v2 §5.1 output contract."""
    skills: list[Skill]
    skill_combinations: list[SkillCombination]
    industries: list[str]
    preferred_work: list[str]
    exclusions: list[str]
    rate_floor_eur_hour: float | None
    availability_hours_week: float | None
    past_employers: list[str]
    schools: list[str]
    languages: list[Literal["nl", "en"]]
    voice_samples: list[str]


# Prompts ---------------------------------------------------------------------
TURN_INSTRUCTIONS = """You interview an independent {segment} in Amsterdam to build an evidence-backed profile from their resume. The profile is used to design fixed-scope freelance services, so you need proof, not adjectives.

Rules:
- Conduct the interview in {language_name}. Ask exactly ONE short question per turn.
- Read the resume first. Never ask what the resume already answers.
- Tie each question to one specific resume line and copy that line exactly into resume_line. For preferred work, exclusions, rate floor, availability and writing samples, resume_line is null.
- Cover topics in this priority: quantified outcomes -> citable clients/employers (may we name them?) -> distinctive skill combinations -> preferred work -> exclusions (work they won't take) -> minimum hourly rate in EUR -> availability in hours per week -> 2-3 short writing samples (e.g. an email or LinkedIn post they wrote) to learn their voice.
- Be engaging: reflect back in one short sentence what was notable in their last answer (empty on the first turn), keep turns short, and estimate honestly how many questions are left.
- Ask about one resume line at most twice. If two answers still give no proof, it stays a claim: move on to the next topic.
- Topics already answered: {covered}. Skip them unless an answer was unclear. If an answer covers several topics at once, count them all as covered.
- Stop (done=true, question=null) once every topic is covered, or after {max_q} questions. Questions asked so far: {asked}.
- Never invent facts about the person."""

MAX_ASKS_PER_LINE = 2

PROFILE_INSTRUCTIONS = """Build the freelancer's profile from their resume and interview answers. Output the JSON contract exactly.

Rules:
- Use ONLY what the resume or the answers say. Never invent employers, clients, schools, numbers, rates or hours. Leave a field empty or null when it is not stated.
- skills: name each skill; evidence items quote or closely paraphrase a concrete claim, with metric (the number as written) and client (as written) only when stated. citable=true only when the freelancer said the client may be named.
- proof_strength: "claimed" = listed without evidence; "demonstrated" = a concrete outcome or project; "referenceable" = a citable client or a reference is available.
- skill_combinations: pairs or triples of skills the evidence shows being used together.
- rate_floor_eur_hour and availability_hours_week: only the numbers the freelancer gave.
- voice_samples: the writing samples exactly as the freelancer pasted them.
- languages: "nl" and/or "en" the freelancer can work in, as stated.
- taxonomy_id: null."""


def _language_name(lang: str) -> str:
    return "Dutch" if lang == "nl" else "English"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Grounding helpers -------------------------------------------------------------
def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-zà-ÿ]+", "", (text or "").lower())


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", text or "")}


def appears_in(value: str, source: str) -> bool:
    """Loose containment: case, spaces and punctuation are ignored."""
    v = _squash(value)
    return bool(v) and v in _squash(source)


def _num_in(value: float, source: str) -> bool:
    nums = _numbers(source)
    as_int = str(int(value)) if float(value).is_integer() else None
    return str(value) in nums or (as_int is not None and as_int in nums)


def ground_profile(profile: Profile, resume: str, answers: str) -> tuple[Profile, list[dict]]:
    """Remove anything not found in the resume or answers. Returns (clean profile, checks)."""
    both = f"{resume}\n{answers}"
    removed: dict[str, list[str]] = {"employers": [], "schools": [], "clients": [], "metrics": [], "voice": []}
    lowered: list[str] = []

    employers = [e for e in profile.past_employers if appears_in(e, both) or removed["employers"].append(e)]
    schools = [s for s in profile.schools if appears_in(s, both) or removed["schools"].append(s)]
    skills = []
    for skill in profile.skills:
        evidence = []
        for ev in skill.evidence:
            client, metric = ev.client, ev.metric
            if client and not appears_in(client, both):
                removed["clients"].append(client)
                client = None
            if metric and not _numbers(metric) <= _numbers(both):
                removed["metrics"].append(metric)
                metric = None
            evidence.append(ev.model_copy(update={"client": client, "metric": metric,
                                                  "citable": ev.citable and client is not None}))
        strength = skill.proof_strength
        if strength != "claimed" and not evidence:
            strength = "claimed"
            lowered.append(skill.name)
        if strength == "referenceable" and not any(e.citable for e in evidence):
            strength = "demonstrated"
            lowered.append(skill.name)
        skills.append(skill.model_copy(update={"evidence": evidence, "proof_strength": strength}))
    voice = [v for v in profile.voice_samples if appears_in(v, answers) or removed["voice"].append(v[:60])]
    rate = profile.rate_floor_eur_hour
    rate_ok = rate is None or _num_in(rate, answers)
    hours = profile.availability_hours_week
    hours_ok = hours is None or _num_in(hours, answers)  # a plan, so only the freelancer's answers count

    clean = profile.model_copy(update={
        "past_employers": employers, "schools": schools, "skills": skills, "voice_samples": voice,
        "rate_floor_eur_hour": rate if rate_ok else None,
        "availability_hours_week": hours if hours_ok else None,
    })

    def check(name: str, items: list[str]) -> dict:
        return {"name": name, "passed": not items, "note": ("Removed: " + "; ".join(items)) if items else None}

    checks = [
        check("Employers and schools are in your CV or answers", removed["employers"] + removed["schools"]),
        check("Clients are in your CV or answers", removed["clients"]),
        check("Numbers are in your CV or answers", removed["metrics"]),
        check("Writing samples are your own words", removed["voice"]),
        {"name": "Proof strength matches the evidence", "passed": not lowered,
         "note": ("Lowered (no evidence, or no client you may name): " + ", ".join(lowered)) if lowered else None},
        {"name": "Rate floor is what you said", "passed": rate_ok,
         "note": None if rate_ok else f"Removed {rate}: not in your answers"},
        {"name": "Availability is what you said", "passed": hours_ok,
         "note": None if hours_ok else f"Removed {hours}: not in your answers"},
        {"name": "Rate floor given", "passed": clean.rate_floor_eur_hour is not None,
         "note": None if clean.rate_floor_eur_hour is not None else "Add your minimum hourly rate before approving"},
    ]
    return clean, checks


# State -------------------------------------------------------------------------
def resume_text(store, settings: Settings) -> tuple[str, str]:
    rel = store.get_setting("cv_text_path")
    if not rel:
        raise ValueError("Upload your CV first")
    path = settings.data_dir / rel
    try:
        return path.read_text(encoding="utf-8"), rel
    except OSError as exc:
        raise ValueError("Your CV text is missing; upload your CV again") from exc


def get_state(store) -> dict | None:
    return store.get_setting(STATE_KEY)


def _transcript(state: dict) -> str:
    lines = []
    for i, t in enumerate(state["turns"], 1):
        lines.append(f"Q{i} [{t['topic']}]: {t['question']}")
        if t.get("answer") is not None:
            lines.append(f"A{i}: {t['answer']}")
    return "\n".join(lines) or "(no questions asked yet)"


def _answers(state: dict) -> str:
    return "\n".join(t["answer"] for t in state["turns"] if t.get("answer"))


def _quote_ok(turn: InterviewTurn, resume: str) -> bool:
    if turn.done or turn.topic in FREE_TOPICS or not turn.resume_line:
        return True
    return appears_in(turn.resume_line, resume)


def _times_asked(state: dict, line: str | None) -> int:
    key = _squash(line or "")
    return sum(1 for t in state["turns"] if key and _squash(t.get("resume_line") or "") == key)


def _turn_problems(turn: InterviewTurn, state: dict, resume: str) -> list[str]:
    problems = []
    if not _quote_ok(turn, resume):
        problems.append("resume_line must be copied exactly from the resume (or null for free topics)")
    if not turn.done and not (turn.question or "").strip():
        problems.append("question must be set unless done")
    if not turn.done and turn.resume_line and _times_asked(state, turn.resume_line) >= MAX_ASKS_PER_LINE:
        problems.append(f"you already asked about this resume line {MAX_ASKS_PER_LINE} times; move to another "
                        "line or the next topic")
    return problems


def _next_turn(state: dict, resume: str, settings: Settings) -> dict:
    asked = len(state["turns"])
    covered = sorted({t["topic"] for t in state["turns"] if t.get("answer")}) or ["none"]
    instructions = TURN_INSTRUCTIONS.format(segment=state["segment"], language_name=_language_name(state["language"]),
                                            max_q=MAX_QUESTIONS, asked=asked, covered=", ".join(covered))
    base = f"RESUME:\n{resume}\n\nINTERVIEW SO FAR:\n{_transcript(state)}"
    turn = ai.structured("smart", settings, instructions=instructions, input=base, schema=InterviewTurn)
    problems = _turn_problems(turn, state, resume)
    if problems:
        feedback = "\n\nYOUR LAST REPLY FAILED A CHECK: " + "; ".join(problems) + "."
        turn = ai.structured("smart", settings, instructions=instructions, input=base + feedback, schema=InterviewTurn)
        if not _quote_ok(turn, resume):
            turn = turn.model_copy(update={"resume_line": None})  # never show a quote that isn't in the CV
    if not turn.done and not (turn.question or "").strip():
        raise ai.AIUnavailable("The interviewer returned no question")
    return turn.model_dump()


def _public(state: dict | None) -> dict:
    if not state:
        return {"status": "not_started", "turns": [], "progress": None}
    asked = len(state["turns"])
    left = 0 if state["status"] != "active" else max(0, min(state.get("questions_left", 0), MAX_QUESTIONS - asked + 1))
    return {"status": state["status"], "language": state["language"], "segment": state["segment"],
            "turns": state["turns"], "progress": {"asked": asked, "left": left, "max": MAX_QUESTIONS},
            "transcript_ref": state.get("transcript_ref")}


def public_state(store) -> dict:
    return {**_public(get_state(store)), "job": jobs.get(store, JOB)}


def start(store, settings: Settings, language: str = "en", segment: str | None = None) -> dict:
    if language not in ("nl", "en"):
        raise ValueError("language must be nl or en")
    resume, resume_ref = resume_text(store, settings)
    state = {"status": "active", "language": language, "segment": segment or SEGMENT_DEFAULT,
             "resume_ref": resume_ref, "turns": [], "questions_left": MAX_QUESTIONS, "started_at": _now()}
    turn = _next_turn(state, resume, settings)
    _apply_turn(state, turn)
    store.set_setting(STATE_KEY, state)
    return public_state(store)


def _apply_turn(state: dict, turn: dict) -> None:
    if turn["done"] or len(state["turns"]) >= MAX_QUESTIONS:
        state["status"] = "complete"
        state["closing"] = turn.get("reflection") or ""
        return
    state["turns"].append({"question": turn["question"].strip(), "resume_line": turn["resume_line"],
                           "reflection": turn["reflection"], "topic": turn["topic"], "answer": None,
                           "asked_at": _now()})
    state["questions_left"] = turn["questions_left"]


def answer(store, settings: Settings, text: str) -> dict:
    state = get_state(store)
    if not state or state["status"] != "active":
        raise ValueError("No interview in progress; start one first")
    text = (text or "").strip()
    if not text:
        raise ValueError("Write an answer first")
    state["turns"][-1]["answer"] = text[:4000]
    state["turns"][-1]["answered_at"] = _now()
    if len(state["turns"]) >= MAX_QUESTIONS:
        state["status"] = "complete"
    else:
        resume, _ = resume_text(store, settings)
        _apply_turn(state, _next_turn(state, resume, settings))
    store.set_setting(STATE_KEY, state)
    return public_state(store)


def finish(store) -> dict:
    """End the interview early (the freelancer has said enough)."""
    state = get_state(store)
    if not state or state["status"] not in ("active", "complete"):
        raise ValueError("No interview in progress")
    if state["turns"] and state["turns"][-1].get("answer") is None:
        state["turns"].pop()  # unanswered question
    state["status"] = "complete"
    store.set_setting(STATE_KEY, state)
    return public_state(store)


# Profile extraction (background job) -------------------------------------------
def run_extract(store, settings: Settings) -> dict:
    state = get_state(store)
    if not state or state["status"] not in ("complete", "extracted"):
        raise ValueError("Finish the interview first")
    resume, resume_ref = resume_text(store, settings)
    answers = _answers(state)

    jobs.step(store, JOB, "transcript", "Saving your interview")
    transcript_ref = save_raw(settings.data_dir, "interview.md", (
        f"# Interview ({state['language']})\n\nResume: {resume_ref}\n\n{_transcript(state)}\n").encode())

    jobs.step(store, JOB, "profile", "Building your profile from your CV and answers")
    base = f"RESUME:\n{resume}\n\nINTERVIEW:\n{_transcript(state)}"
    profile = ai.structured("smart", settings, instructions=PROFILE_INSTRUCTIONS, input=base, schema=Profile)
    clean, checks = ground_profile(profile, resume, answers)
    if any(not c["passed"] for c in checks[:4]):  # invented items: retry once with the failures
        notes = "; ".join(c["note"] for c in checks[:4] if c["note"])
        profile = ai.structured("smart", settings, instructions=PROFILE_INSTRUCTIONS,
                                input=base + f"\n\nYOUR LAST PROFILE INCLUDED ITEMS NOT IN THE SOURCES ({notes}). "
                                             "Use only what the resume and answers say.", schema=Profile)
        clean, checks = ground_profile(profile, resume, answers)

    provenance = {"model": ai.model_for("smart", settings), "prompt_version": PROFILE_PROMPT_VERSION,
                  "input_ref": [resume_ref, transcript_ref], "ts": _now()}
    saved = versions.save(store.conn, "profile", clean.model_dump(), author="agent",
                          provenance=provenance, checks=checks)
    state["status"] = "extracted"
    state["transcript_ref"] = transcript_ref
    store.set_setting(STATE_KEY, state)
    jobs.done(store, JOB, "Your profile is ready to review", {"version": saved["version"]})
    return saved
