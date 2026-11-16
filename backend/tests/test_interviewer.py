"""Interviewer (spec v2 §5.1) and profile approval. Fake model; no OpenAI calls."""

import json

import pytest
from fastapi.testclient import TestClient

from app import profiles
from app.ai import interviewer
from app.ai.interviewer import Evidence, InterviewTurn, Profile, Skill, ground_profile
from app.config import REPO_ROOT, Settings
from app.main import create_app

RESUME = """Sam de Vries, data engineer, Amsterdam.
2021-2026 Data engineer at Tulip Data B.V.: rebuilt the dbt models, cut nightly load time by 40%.
2018-2021 Analyst at Grachtbank. MSc Data Science, Universiteit van Amsterdam.
Skills: Python, SQL, dbt, Airflow."""


def turn(question="What did the 40% cut change for the business?", line="cut nightly load time by 40%",
         topic="outcomes", done=False, left=5):
    return InterviewTurn(done=done, reflection="", question=None if done else question,
                         resume_line=None if done else line, topic=topic, questions_left=left)


def make_profile(**over):
    base = dict(
        skills=[Skill(name="dbt", taxonomy_id=None, proof_strength="referenceable",
                      evidence=[Evidence(claim="Rebuilt the dbt models", metric="40%", client="Tulip Data", citable=True)]),
                Skill(name="Airflow", taxonomy_id=None, proof_strength="demonstrated", evidence=[])],
        skill_combinations=[], industries=["fintech"], preferred_work=["data platform rebuilds"],
        exclusions=["CMS maintenance"], rate_floor_eur_hour=95, availability_hours_week=24,
        past_employers=["Tulip Data B.V.", "Grachtbank"], schools=["Universiteit van Amsterdam"],
        languages=["nl", "en"], voice_samples=["Hi Lisa, quick one: can we move the review to Thursday?"])
    base.update(over)
    return Profile(**base)


ANSWERS = ("It cut cloud costs and Tulip Data said I may name them. I want data platform rebuilds, no CMS "
           "maintenance. My minimum is 95 euro per hour, 24 hours a week. Sample: Hi Lisa, quick one: can we "
           "move the review to Thursday?")


# Grounding (plain code) -------------------------------------------------------
def test_grounded_profile_passes_untouched():
    clean, checks = ground_profile(make_profile(), RESUME, ANSWERS)
    assert all(c["passed"] for c in checks if c["name"] != "Proof strength matches the evidence")
    assert clean.past_employers == ["Tulip Data B.V.", "Grachtbank"]
    assert clean.skills[0].proof_strength == "referenceable"
    assert clean.rate_floor_eur_hour == 95


def test_invented_items_are_removed_and_listed():
    invented = make_profile(
        past_employers=["Tulip Data B.V.", "Adyen"], schools=["TU Delft"],
        skills=[Skill(name="dbt", taxonomy_id=None, proof_strength="referenceable",
                      evidence=[Evidence(claim="x", metric="70%", client="Booking.com", citable=True)])],
        voice_samples=["A sample they never wrote"], rate_floor_eur_hour=120, availability_hours_week=40)
    clean, checks = ground_profile(invented, RESUME, ANSWERS)
    by = {c["name"]: c for c in checks}
    assert clean.past_employers == ["Tulip Data B.V."] and clean.schools == []
    assert "Adyen" in by["Employers and schools are in your CV or answers"]["note"]
    ev = clean.skills[0].evidence[0]
    assert ev.client is None and ev.metric is None and ev.citable is False
    assert clean.skills[0].proof_strength == "demonstrated"  # no citable client left
    assert clean.voice_samples == [] and not by["Writing samples are your own words"]["passed"]
    assert clean.rate_floor_eur_hour is None and not by["Rate floor is what you said"]["passed"]
    assert clean.availability_hours_week is None


def test_skill_without_evidence_is_lowered_to_claimed():
    clean, checks = ground_profile(make_profile(), RESUME, ANSWERS)
    assert clean.skills[1].proof_strength == "claimed"
    assert "Airflow" in next(c for c in checks if c["name"].startswith("Proof strength"))["note"]


# Interview flow ----------------------------------------------------------------
class FakeModel:
    """Stands in for ai.structured: returns queued replies per schema and records the calls."""

    def __init__(self, turns=(), profiles=()):
        self.turns, self.profiles, self.calls = list(turns), list(profiles), []

    def __call__(self, role, settings, *, instructions, input, schema, tools=None):
        self.calls.append({"role": role, "schema": schema.__name__, "input": input, "instructions": instructions})
        queue = self.turns if schema is InterviewTurn else self.profiles
        return queue.pop(0) if len(queue) > 1 else queue[0]


def settings(tmp_path):
    return Settings(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    openai_key_set=True, model_smart="smart-model", model_check="m", model_fast="m")


@pytest.fixture()
def app_and_client(tmp_path, monkeypatch):
    from app.guards import RateLimiter
    app = create_app(settings(tmp_path))
    app.state.action_limiter = RateLimiter(max_calls=1000, per_seconds=60)  # the cap test sends 16 actions
    with TestClient(app) as c:
        yield app, c


def upload_cv(c):
    assert c.post("/api/cv", files={"file": ("Sam CV.txt", RESUME.encode(), "text/plain")}).status_code == 200


def test_start_needs_a_cv(app_and_client, monkeypatch):
    _, c = app_and_client
    monkeypatch.setattr(interviewer.ai, "structured", FakeModel([turn()]))
    res = c.post("/api/interview/start", json={"language": "en"})
    assert res.status_code == 409 and "Upload your CV" in res.json()["error"]["message"]


def test_one_question_at_a_time_tied_to_a_resume_line(app_and_client, monkeypatch):
    _, c = app_and_client
    upload_cv(c)
    fake = FakeModel([turn()])
    monkeypatch.setattr(interviewer.ai, "structured", fake)
    state = c.post("/api/interview/start", json={"language": "nl"}).json()
    assert state["status"] == "active" and len(state["turns"]) == 1
    assert state["turns"][0]["resume_line"] == "cut nightly load time by 40%"
    assert state["progress"] == {"asked": 1, "left": 5, "max": 15}
    assert fake.calls[0]["role"] == "smart" and "Dutch" in fake.calls[0]["instructions"]
    assert "Tulip Data" in fake.calls[0]["input"]  # the resume is read first


def test_a_made_up_resume_quote_is_retried_then_dropped(app_and_client, monkeypatch):
    _, c = app_and_client
    upload_cv(c)
    bad = turn(line="Led a team of 30 engineers at Google")
    fake = FakeModel([bad, bad])
    monkeypatch.setattr(interviewer.ai, "structured", fake)
    state = c.post("/api/interview/start", json={}).json()
    assert len(fake.calls) == 2 and "FAILED A CHECK" in fake.calls[1]["input"]
    assert state["turns"][0]["resume_line"] is None


def test_interview_stops_at_the_question_cap(app_and_client, monkeypatch):
    _, c = app_and_client
    upload_cv(c)
    monkeypatch.setattr(interviewer.ai, "structured",
                        FakeModel([turn(topic="other", line=None)], [make_profile()]))
    c.post("/api/interview/start", json={})
    for i in range(interviewer.MAX_QUESTIONS):
        c.post("/api/interview/answer", json={"answer": f"answer {i}"})
    state = c.get("/api/interview").json()
    assert state["status"] in ("complete", "extracted")
    assert state["progress"]["asked"] == interviewer.MAX_QUESTIONS
    assert c.post("/api/interview/answer", json={"answer": "one more"}).status_code == 409


def test_full_flow_profile_draft_then_approval(app_and_client, monkeypatch):
    app, c = app_and_client
    upload_cv(c)
    fake = FakeModel([turn(), turn(done=True)], [make_profile()])
    monkeypatch.setattr(interviewer.ai, "structured", fake)
    monkeypatch.setattr(interviewer.ai, "model_for", lambda role, s: "smart-model")
    c.post("/api/interview/start", json={})
    assert c.post("/api/interview/answer", json={"answer": ANSWERS}).json()["status"] == "complete"
    state = c.get("/api/interview").json()  # the profile build runs after the response
    assert state["status"] == "extracted" and state["job"]["status"] == "done"
    assert state["transcript_ref"].startswith("raw/") and state["transcript_ref"].endswith("interview.md")

    cur = c.get("/api/profile").json()
    assert cur["approved"] is None and cur["latest"]["status"] == "draft" and cur["latest"]["author"] == "agent"
    prov = cur["latest"]["provenance"]
    assert prov["model"] == "smart-model" and prov["prompt_version"] == interviewer.PROFILE_PROMPT_VERSION
    assert prov["input_ref"][1] == state["transcript_ref"]
    assert c.get("/api/fit").json()["rate"]["floor"] is None  # nothing approved: no floor yet

    ok = c.post("/api/profile/approve", json={"version": cur["latest"]["version"]}).json()
    assert ok["status"] == "approved"
    store = app.state.store
    assert profiles.rate_floor(store) == 95
    assert c.get("/api/fit").json()["rate"]["floor"] == 95
    assert store.get_setting("profile_exclusions") == ["CMS maintenance"]
    persona = c.get("/api/persona").json()
    assert persona["skills"] == ["dbt", "Airflow"] and persona["hours_per_week"] == 24
    assert '"profile_approved"' in (store.data_dir / "events.jsonl").read_text()
    assert c.post("/api/profile/approve", json={"version": ok["version"]}).status_code == 409  # not a draft


def test_approval_needs_a_rate_floor_and_user_edits_are_new_versions(app_and_client, monkeypatch):
    app, c = app_and_client
    data = make_profile(rate_floor_eur_hour=None).model_dump()
    v1 = c.put("/api/profile", json=data).json()
    assert v1["author"] == "user" and not v1["checks"][0]["passed"]
    res = c.post("/api/profile/approve", json={"version": v1["version"]})
    assert res.status_code == 409 and "minimum hourly rate" in res.json()["error"]["message"]

    v2 = c.put("/api/profile", json={**data, "rate_floor_eur_hour": 110}).json()
    assert v2["version"] == v1["version"] + 1
    assert c.post("/api/profile/approve", json={"version": v2["version"]}).json()["status"] == "approved"
    assert profiles.rate_floor(app.state.store) == 110


def test_profile_edit_rejects_unknown_shapes(app_and_client):
    _, c = app_and_client
    bad = {**make_profile().model_dump(), "languages": ["de"]}
    assert c.put("/api/profile", json=bad).status_code == 422


def test_the_same_resume_line_is_not_asked_a_third_time(app_and_client, monkeypatch):
    _, c = app_and_client
    upload_cv(c)
    same = turn()
    other = turn(question="Which clients may we name?", line="2018-2021 Analyst at Grachtbank.", topic="clients")
    fake = FakeModel([same, same, same, other])
    monkeypatch.setattr(interviewer.ai, "structured", fake)
    c.post("/api/interview/start", json={})
    c.post("/api/interview/answer", json={"answer": "Not sure"})
    state = c.post("/api/interview/answer", json={"answer": "Still not sure"}).json()
    assert "already asked about this resume line 2 times" in fake.calls[-1]["input"]
    assert state["turns"][-1]["resume_line"] == "2018-2021 Analyst at Grachtbank."
    assert "Topics already answered: outcomes" in fake.calls[-1]["instructions"]
