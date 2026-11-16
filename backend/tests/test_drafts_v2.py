"""Writer + Verifier (spec v2 §5.4-5.5, §6.4). Pre-checks are plain functions; fake models; no network."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import profiles, service_catalog
from app.ai import drafts, prechecks
from app.ai.drafts import Issue, Review, WriterDraft
from app.config import REPO_ROOT, Settings
from app.guards import RateLimiter
from app.main import create_app
from app.storage import versions
from app.storage.wiki import save_raw

NOW = datetime.now(timezone.utc)
D = lambda n: (NOW - timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731

PAGE = "Kanaal Analytics heeft 8 miljoen euro opgehaald om het dataplatform uit te breiden. CEO Eva Smit."
PROFILE = {
    "skills": [{"name": "dbt", "taxonomy_id": None, "proof_strength": "referenceable",
                "evidence": [{"claim": "Rebuilt the dbt models and cut nightly load time by 40%", "metric": "40%",
                              "client": "Grachtbank", "citable": True}]},
               {"name": "Python", "taxonomy_id": None, "proof_strength": "demonstrated",
                "evidence": [{"claim": "Built ingestion pipelines in Python", "metric": None, "client": None, "citable": False}]}],
    "skill_combinations": [], "industries": [], "preferred_work": [], "exclusions": [],
    "rate_floor_eur_hour": 95, "availability_hours_week": 24, "past_employers": [], "schools": [],
    "languages": ["nl", "en"], "voice_samples": ["Hi Lisa, quick one: the export is live."],
}
SERVICE = {"id": "dbt-rebuild", "name": "dbt model rebuild", "target_buyer": "Scale-ups", "problem": "Slow loads",
           "deliverable": "a migrated dbt project with tests", "duration_weeks": 6, "skills_used": ["dbt", "Python"],
           "rate_range_eur": {"min": 110, "max": 130}, "demand_index": 0.5, "proof_strength": "referenceable",
           "independence_rationale": "Fixed scope", "rationale": "x",
           "discovery_terms": {"nl": ["data engineer", "dbt"], "en": ["data engineer", "analytics engineer"]}}
NOW_TS = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
LEAD = {"title": "Senior Data Engineer", "company": "Kanaal Analytics", "source": "recruitee",
        "url": "https://kanaal.recruitee.com/o/sde", "apply_url": "https://kanaal.recruitee.com/o/sde/c/new",
        "fetched": True, "status": "filtered", "dedup_key": "recruitee:77", "source_posting_id": "recruitee:77",
        "language": "nl", "published_at": NOW_TS, "description": "Wij bouwen ons dataplatform opnieuw met dbt.",
        "skills": ["dbt", "Python"]}

GOOD_NL = ("Kanaal Analytics haalde net 8 miljoen euro op om het dataplatform uit te breiden. Ik kan jullie dbt-modellen "
           "in 6 weken herbouwen als afgebakend project, met tests en overdracht, terwijl jullie werven. Bij Grachtbank "
           "verkortte ik de nachtelijke laadtijd met 40%. Zal ik een scope van één pagina sturen?")


def wd(body=GOOD_NL, claims=("Rebuilt the dbt models and cut nightly load time by 40%",), subject=None, channel="apply_interim"):
    return WriterDraft(channel=channel, language="nl", subject=subject, body=body, forwardable_blurb=None,
                       claims_used=list(claims), hook_source_url="https://news.example.nl/kanaal")


OK = Review(passed=True, issues=[], revision_notes=None)


class Fake:
    def __init__(self, writes, reviews=(OK,)):
        self.writes, self.reviews, self.calls = list(writes), list(reviews), []

    def __call__(self, role, settings, *, instructions, input, schema, tools=None):
        self.calls.append({"role": role, "schema": schema.__name__, "instructions": instructions})
        q = self.writes if schema is WriterDraft else self.reviews
        return q.pop(0) if len(q) > 1 else q[0]


class FakeFetcher:
    def __init__(self, status=200):
        self.status = status

    def fetch(self, url):
        return {"status": self.status, "text": "", "url": url}


def settings(tmp_path):
    return Settings(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    openai_key_set=True, model_smart="writer-model", model_check="verifier-model", model_fast="f")


@pytest.fixture()
def env(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.action_limiter = RateLimiter(max_calls=1000, per_seconds=60)
    with TestClient(app) as c:
        store = app.state.store
        profiles.approve(store, profiles.edit(store, PROFILE)["version"])
        sv = versions.save(store.conn, "services", {"services": [SERVICE], "market": None}, author="agent")
        service_catalog.approve(store, sv["version"])
        lead = store.upsert_leads([dict(LEAD)])[0]
        raw = save_raw(store.data_dir, "source-news.example.nl.txt", f"URL: x\n\n{PAGE}".encode())
        store.save_brief(lead["id"], {
            "fit": True, "fit_reason": "dbt rebuild", "service_id": "dbt-rebuild",
            "hook": {"summary": "Kanaal Analytics haalde 8 miljoen euro op", "source_url": "https://news.example.nl/kanaal",
                     "published_at": D(5), "type": "funding", "raw": raw},
            "problem_hypothesis": "They probably need the platform rebuilt", "channel": "apply_interim",
            "warm_connection": None, "draft_language": "nl", "sources": []}, "researched")
        yield c, store, store.lead(lead["id"]), tmp_path


def make(store, tmp_path, lead, fake, monkeypatch, status=200, language=None):
    monkeypatch.setattr(drafts.ai, "structured", fake)
    return drafts.write_and_verify(store, settings(tmp_path), lead, language, fetcher=FakeFetcher(status))


# Pre-checks (plain functions) ------------------------------------------------------
def test_prechecks_pass_a_good_draft():
    brief = {"channel": "apply_interim", "hook": {"published_at": D(5)}}
    checks = prechecks.run_all(wd().model_dump(), brief, LEAD, PROFILE, SERVICE, PAGE, 200, 95, NOW)
    assert all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]


@pytest.mark.parametrize("body, failing", [
    (GOOD_NL.replace("8 miljoen", "12 miljoen"), "Hook facts are on the source page"),
    ("Kanaal Analytics en Adyen haalden 8 miljoen op. " + GOOD_NL.split(". ", 1)[1], "Hook facts are on the source page"),
    (GOOD_NL + " " + "woord " * 80, "At most 120 words"),
    (GOOD_NL.replace("Zal ik", "Hoi [NAAM], zal ik"), "No unfilled placeholders"),
    (GOOD_NL + " Ik kan 32 uur per week versterking van uw team zijn.", "Independent project framing (no employment phrases)"),
    (GOOD_NL.replace("40%", "65%"), "Every number comes from a source"),
    (GOOD_NL + " Mijn tarief is €80 per uur.", "No rate below your floor"),
])
def test_prechecks_catch_each_problem(body, failing):
    brief = {"channel": "apply_interim", "hook": {"published_at": D(5)}}
    checks = {c["name"]: c for c in prechecks.run_all(wd(body).model_dump(), brief, LEAD, PROFILE, SERVICE, PAGE, 200, 95, NOW)}
    assert not checks[failing]["passed"], checks[failing]


def test_prechecks_hook_link_date_claims_subject_and_opt_out():
    brief = {"channel": "direct_note", "hook": {"published_at": D(45)}}
    d = wd(claims=["Led a team of 40 at Booking"], subject="A very long subject line that keeps going on",
           channel="direct_note").model_dump()
    checks = {c["name"]: c for c in prechecks.run_all(d, brief, LEAD, PROFILE, SERVICE, PAGE, 404, 95, NOW)}
    for name in ("Hook source link works", "Hook is from the last 30 days", "Every proof point is in your profile evidence",
                 "Subject of 1-7 words", "Direct note has an opt-out line"):
        assert not checks[name]["passed"], name
    # The exact opt-out lines the Writer is told to use must pass
    for line in ("Niet relevant? Laat het weten, dan benader ik je niet meer.",
                 "If this isn't relevant, just say so and I won't follow up."):
        assert prechecks.opt_out(f"{GOOD_NL} {line}", "direct_note")["passed"], line
    assert prechecks.opt_out(GOOD_NL, "apply_interim") is None


# Writer + Verifier loop ----------------------------------------------------------
def test_good_draft_is_ready_after_one_round(env, monkeypatch):
    c, store, lead, tmp = env
    fake = Fake([wd()])
    d = make(store, tmp, lead, fake, monkeypatch)
    assert d["status"] == "ready" and d["round"] == 1 and d["language"] == "nl" and d["channel"] == "apply_interim"
    assert [x["role"] for x in fake.calls] == ["smart", "check"]           # writer never grades itself
    assert d["verifier"]["pass"] is True and d["verifier"]["provenance"]["verifier"]["model"] == "verifier-model"
    assert any("not legal advice" in g for g in d["verifier"]["guidance"])
    assert store.lead(lead["id"])["status"] == "ready"


def test_precheck_failure_goes_back_to_the_writer_without_a_review(env, monkeypatch):
    c, store, lead, tmp = env
    fake = Fake([wd(GOOD_NL.replace("40%", "65%")), wd()])
    d = make(store, tmp, lead, fake, monkeypatch)
    assert d["status"] == "ready" and d["round"] == 2
    assert [x["role"] for x in fake.calls] == ["smart", "smart", "check"]
    assert "unsupported numbers: 65" in fake.calls[1]["instructions"]


def test_review_major_issue_triggers_revision(env, monkeypatch):
    c, store, lead, tmp = env
    generic = Review(passed=False, issues=[Issue(severity="major", check="Specificity", detail="Fits any company")],
                     revision_notes="Tie it to the funding round")
    fake = Fake([wd(), wd()], [generic, OK])
    d = make(store, tmp, lead, fake, monkeypatch)
    assert d["status"] == "ready" and d["round"] == 2
    assert "Fits any company" in fake.calls[2]["instructions"] and "Tie it to the funding round" in fake.calls[2]["instructions"]


def test_still_failing_after_two_revisions_needs_human(env, monkeypatch):
    c, store, lead, tmp = env
    bad = wd(GOOD_NL + " Ik kan fulltime meedraaien.")
    fake = Fake([bad, bad, bad])
    d = make(store, tmp, lead, fake, monkeypatch)
    assert d["status"] == "needs_human" and d["round"] == 3
    assert len([x for x in fake.calls if x["role"] == "smart"]) == 3 and not any(x["role"] == "check" for x in fake.calls)
    failed = [x for x in d["verifier"]["checks"] if not x["passed"]]
    assert failed and "fulltime" in failed[0]["note"]
    assert store.lead(lead["id"])["status"] == "drafted"
    assert '"verifier_failed"' in (store.data_dir / "events.jsonl").read_text()


def test_minor_issues_still_pass(env, monkeypatch):
    c, store, lead, tmp = env
    minor = Review(passed=True, issues=[Issue(severity="minor", check="Tone", detail="Slightly formal")], revision_notes=None)
    d = make(store, tmp, lead, Fake([wd()], [minor]), monkeypatch)
    assert d["status"] == "ready" and {"name": "Reviewer: Tone", "passed": True, "note": "Slightly formal"} in d["verifier"]["checks"]


def test_code_sets_channel_and_language_not_the_writer(env, monkeypatch):
    c, store, lead, tmp = env
    sneaky = WriterDraft(channel="direct_note", language="en", subject="Hello", body=GOOD_NL, forwardable_blurb="x",
                         claims_used=["Rebuilt the dbt models and cut nightly load time by 40%"], hook_source_url="x")
    d = make(store, tmp, lead, Fake([sneaky]), monkeypatch)
    assert d["channel"] == "apply_interim" and d["language"] == "nl" and "subject" not in d or d.get("subject") is None


def test_regenerating_in_the_other_language_keeps_both_and_supersedes_same_language(env, monkeypatch):
    c, store, lead, tmp = env
    first = make(store, tmp, lead, Fake([wd()]), monkeypatch)
    en = make(store, tmp, lead, Fake([wd()]), monkeypatch, language="en")
    again = make(store, tmp, lead, Fake([wd()]), monkeypatch)
    statuses = {d["id"]: d["status"] for d in store.drafts()["drafts"]}
    assert statuses[first["id"]] == "superseded" and statuses[en["id"]] == "ready" and statuses[again["id"]] == "ready"
    assert en["language"] == "en"


def test_unresearched_lead_is_refused(env, monkeypatch):
    c, store, lead, tmp = env
    other = store.upsert_leads([{**LEAD, "dedup_key": "recruitee:99", "source_posting_id": "recruitee:99", "url": "https://k/2"}])[0]
    with pytest.raises(ValueError, match="Research this lead first"):
        make(store, tmp, store.lead(other["id"]), Fake([wd()]), monkeypatch)


# Routes: draft job, copy (the freelancer's approval), nothing sent ----------------
def test_draft_route_then_copy_records_edits(env, monkeypatch):
    c, store, lead, tmp = env
    monkeypatch.setattr(drafts.ai, "structured", Fake([wd()]))
    monkeypatch.setattr(drafts, "PageFetcher", lambda key: FakeFetcher())
    assert c.post(f"/api/leads/{lead['id']}/draft", json={}).status_code == 202
    job = c.get(f"/api/leads/{lead['id']}/draft/status").json()
    assert job["status"] == "done" and job["result"]["status"] == "ready"
    draft_id = job["result"]["draft_id"]

    edited = GOOD_NL.replace("Zal ik", "Zullen we")
    out = c.post(f"/api/drafts/{draft_id}/copied", json={"final_text": edited}).json()
    assert out["status"] == "copied" and out["final_text"] == edited and out["copied_at"]
    assert store.lead(lead["id"])["status"] == "copied"
    events = (store.data_dir / "events.jsonl").read_text()
    assert '"draft_copied"' in events and '"draft_edited"' in events
    assert store.conn.execute("SELECT COUNT(*) FROM queue WHERE kind = 'send'").fetchone()[0] == 0  # nothing queued to send


def test_batch_drafts_researched_leads(env, monkeypatch):
    c, store, lead, tmp = env
    monkeypatch.setattr(drafts.ai, "structured", Fake([wd()]))
    monkeypatch.setattr(drafts, "PageFetcher", lambda key: FakeFetcher())
    out = drafts.run_batch(store, settings(tmp), limit=5)
    assert out == {"ready": 1, "needs_human": 0, "failed": 0}
    assert store.leads_to_draft(5) == []


def test_prechecks_accept_float_durations_hook_dates_and_dutch_compounds():
    brief = {"channel": "apply_interim", "hook": {"published_at": D(5)}}
    service = {**SERVICE, "duration_weeks": 8.0, "rate_range_eur": {"min": 110.0, "max": 130.0}}
    day = int(D(5)[8:10])
    body = (f"Op {day} november haalde Kanaal Analytics 8 miljoen euro op voor AI-platformimplementaties. "
            "Ik herbouw jullie dbt-modellen in 8 weken als afgebakend project. Bij Grachtbank verkortte ik de laadtijd "
            "met 40%. Zal ik een scope sturen?")
    page = PAGE + " AI platform"
    checks = {c["name"]: c for c in prechecks.run_all(wd(body).model_dump(), brief, LEAD, PROFILE, service, page, 200, 95, NOW)}
    assert checks["Every number comes from a source"]["passed"], checks["Every number comes from a source"]
    assert checks["Hook facts are on the source page"]["passed"], checks["Hook facts are on the source page"]
