"""Profile AI (app/ai/profile.py) with a fake `ai.structured`: no network, no OpenAI."""

import json

import pytest
from fastapi.testclient import TestClient

from app.ai import profile
from app.config import REPO_ROOT, Settings
from app.main import create_app


def settings(tmp_path):
    return Settings(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    # key "not set" so app startup skips the live model listing; ai.structured is faked anyway
                    openai_key_set=False, model_smart="m", model_check="m", model_fast="m")


PERSONA = profile.PersonaExtraction(
    name="Sam Verhoeven", role="Senior Project Manager", years=10, location="Amsterdam, Netherlands",
    skills=["System roll-outs", "Project recovery", "PRINCE2"], wants="", avoids="",
    quotes=[profile.Quote(field="role", quote="Senior Project Manager · Amsterdam, Netherlands"),
            profile.Quote(field="years", quote="I made this up")],
)
EMPTY_PERSONA = profile.PersonaExtraction(name="", role="", years=None, location="", skills=[], wants="",
                                          avoids="", quotes=[])


def fit_proposal(cv_source, low=110, ask=120, high=140, floor=100, reason_source="data/benchmarks.json#hourly"):
    return profile.FitProposal(
        fits=[
            profile.FitItem(name="Interim system roll-outs", level="high",
                            reason="Led multi-site roll-outs", sources=[cv_source]),
            profile.FitItem(name="Project rescue", level="medium", reason="Rescued a stalled project",
                            sources=[cv_source]),
        ],
        rate=profile.RateProposal(floor=floor, low=low, ask=ask, high=high, reasons=[
            profile.RateReason(text="Boston agencies bill $115–135/hour", source=reason_source),
            profile.RateReason(text="Break-even is $120–145/hour", source="data/benchmarks.json#salary_equivalent"),
        ]),
    )


OFFERS = profile.OfferSet(offers=[
    profile.OfferItem(name="Roll-out lead", pitch="Lead a system roll-out end to end.",
                      price="$12,000 fixed (≈ $125/hour)", evidence="17 posts mention roll-outs",
                      fit="Led roll-outs across 6 distribution centres; 99 sites"),
    profile.OfferItem(name="Project rescue", pitch="Get a stalled project back on track in 3 weeks.",
                      price="$6,000 fixed", evidence="[N] posts mention rescue", fit="10 years of delivery"),
    profile.OfferItem(name="Fractional PM", pitch="10 hours a week.", price="$5,000 / month",
                      evidence="Demand for part-time PMs", fit="Matches available hours"),
])


class FakeAI:
    """Answers ai.structured by schema; each schema has a queue of answers (the last one repeats)."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def __call__(self, role, settings, *, instructions, input, schema, tools=None):
        self.calls.append({"role": role, "schema": schema.__name__, "input": input})
        queue = self.answers[schema.__name__]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        return answer(input) if callable(answer) else answer

    def count(self, schema_name):
        return sum(1 for c in self.calls if c["schema"] == schema_name)


def cv_source_from(input_text):
    """The fake fit model cites the CV path it was given, as a well-behaved model would."""
    allowed = json.loads(input_text.split("\n\nCV text")[0].split("\n\nYour previous answer")[0])["allowed_sources"]
    return next(s for s in allowed if s.startswith("raw/") and s.endswith(".txt"))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    def make(fake):
        monkeypatch.setattr(profile.ai, "structured", fake)
        return TestClient(create_app(settings(tmp_path)))
    return make


def build(client):
    assert client.post("/api/profile/build").status_code == 202
    return client.get("/api/profile/status").json()  # TestClient runs background tasks before returning


def test_happy_path_through_the_route(app, tmp_path):
    fake = FakeAI(PersonaExtraction=[PERSONA], FitProposal=[lambda i: fit_proposal(cv_source_from(i))],
                  OfferSet=[OFFERS])
    with app(fake) as c:
        assert c.post("/api/sample-cv/use").status_code == 200
        job = build(c)
        assert job["status"] == "done", job
        assert job["result"] == {"checks_passed": True}
        persona, fit, offers = c.get("/api/persona").json(), c.get("/api/fit").json(), c.get("/api/offers").json()

    assert all(call["role"] == "smart" for call in fake.calls)
    assert [fake.count(s) for s in ("PersonaExtraction", "FitProposal", "OfferSet")] == [1, 1, 1]
    assert persona["role"] == "Senior Project Manager" and persona["years"] == 10
    assert persona["sources"]  # the uploaded CV is still listed

    # Same shape as fixtures/fit.json (minus the _sample marker)
    sample_fit = json.loads((REPO_ROOT / "fixtures" / "fit.json").read_text())
    assert set(fit) == set(sample_fit) - {"_sample"}
    assert set(fit["rate"]) == set(sample_fit["rate"])
    assert set(fit["fits"][0]) == set(sample_fit["fits"][0])
    assert fit["rate"] | {"reasons": None} == {"floor": 100, "ask": 120, "low": 110, "high": 140,
                                               "currency": "USD", "unit": "hour", "reasons": None}
    assert all(ch["passed"] for ch in fit["goal_checks"]) and len(fit["goal_checks"]) == 5

    sample_offers = json.loads((REPO_ROOT / "fixtures" / "offers.json").read_text())
    assert len(offers["offers"]) == 3
    assert all(set(o) == set(sample_offers["offers"][0]) for o in offers["offers"])
    assert len({o["id"] for o in offers["offers"]}) == 3
    first = offers["offers"][0]
    assert first["id"] == "roll-out-lead"
    assert first["evidence"] == "[N] posts mention roll-outs"  # no invented counts
    assert first["fit"] == "Led roll-outs across 6 distribution centres; [N] sites"  # 6 is in the CV, 99 isn't

    wiki = tmp_path / "data" / "wiki"
    persona_md, rate_md = (wiki / "persona.md").read_text(), (wiki / "rate.md").read_text()
    assert "Senior Project Manager" in persona_md and "raw/" in persona_md
    assert "I made this up" not in persona_md  # quotes not found in the CV are dropped
    assert "$110–140/hour" in rate_md and "data/benchmarks.json#hourly" in rate_md
    index = (wiki / "index.md").read_text()
    assert "[persona.md](persona.md)" in index and "[rate.md](rate.md)" in index
    assert "profile built" in (wiki / "log.md").read_text()


def test_fit_failing_code_checks_is_retried_then_saved_with_passed_false(app):
    # Below the floor and citing a source that isn't allowed, every time
    bad = profile.FitProposal(
        fits=[profile.FitItem(name="Anything", level="high", reason="r", sources=["https://example.com"])],
        rate=profile.RateProposal(floor=80, low=90, ask=95, high=300,
                                  reasons=[profile.RateReason(text="t", source="glassdoor")]),
    )
    fake = FakeAI(PersonaExtraction=[PERSONA], FitProposal=[bad], OfferSet=[OFFERS])
    with app(fake) as c:
        c.post("/api/sample-cv/use")
        job = build(c)
        fit = c.get("/api/fit").json()

    assert fake.count("FitProposal") == 3  # first try + two retries
    retry_inputs = [call["input"] for call in fake.calls if call["schema"] == "FitProposal"][1:]
    assert all("It failed these checks" in i and "Floor matches goal.md" in i for i in retry_inputs)
    assert job["status"] == "done" and job["result"] == {"checks_passed": False}
    assert "checks didn't pass" in job["message"]
    failed = {ch["name"] for ch in fit["goal_checks"] if not ch["passed"]}
    assert failed == {"Floor matches goal.md", "Floor ≤ low ≤ ask ≤ high", "Range inside the benchmarks",
                      "Every source is allowed"}
    assert all(ch.get("note") for ch in fit["goal_checks"] if not ch["passed"])


def test_fit_retry_that_fixes_the_checks_passes(app):
    answers = [lambda i: fit_proposal(cv_source_from(i), low=95),  # low below the floor
               lambda i: fit_proposal(cv_source_from(i))]
    fake = FakeAI(PersonaExtraction=[PERSONA], FitProposal=answers, OfferSet=[OFFERS])
    with app(fake) as c:
        c.post("/api/sample-cv/use")
        assert build(c)["result"] == {"checks_passed": True}
        fit = c.get("/api/fit").json()
    assert fake.count("FitProposal") == 2
    assert fit["rate"]["low"] == 110 and all(ch["passed"] for ch in fit["goal_checks"])


def test_persona_without_cv_builds_fit_and_offers(app):
    fake = FakeAI(PersonaExtraction=[PERSONA],
                  FitProposal=[fit_proposal("data/benchmarks.json#hourly")], OfferSet=[OFFERS])
    with app(fake) as c:
        c.put("/api/persona", json={"role": "Project manager", "skills": ["PRINCE2"], "hours_per_week": 20})
        job = build(c)
        persona = c.get("/api/persona").json()
        fit = c.get("/api/fit").json()
        offers = c.get("/api/offers").json()
    assert job["status"] == "done", job
    assert fake.count("PersonaExtraction") == 0  # nothing to read; the interview's persona is used as is
    assert persona["role"] == "Project manager" and persona["hours_per_week"] == 20
    fit_input = next(call["input"] for call in fake.calls if call["schema"] == "FitProposal")
    assert '"Project manager"' in fit_input
    assert fit["rate"]["ask"] == 120 and len(offers["offers"]) == 3


def test_no_cv_and_no_persona_fails_with_a_clear_message(app):
    fake = FakeAI(PersonaExtraction=[PERSONA], FitProposal=[fit_proposal("x")], OfferSet=[OFFERS])
    with app(fake) as c:
        job = build(c)
    assert job["status"] == "error"
    assert "Upload your CV" in job["error"]
    assert fake.calls == []


def test_cv_keeps_users_hours_income_and_existing_fields(app):
    sparse = profile.PersonaExtraction(name="Sam Verhoeven", role="", years=None, location="",
                                       skills=["PRINCE2", "Budgeting"], wants="", avoids="", quotes=[])
    fake = FakeAI(PersonaExtraction=[sparse], FitProposal=[lambda i: fit_proposal(cv_source_from(i))],
                  OfferSet=[OFFERS])
    with app(fake) as c:
        c.put("/api/persona", json={"role": "Interim PM", "wants": "Rescue work", "skills": ["Prince2", "Coaching"],
                                    "hours_per_week": 28, "income_goal_monthly": 9000})
        c.post("/api/sample-cv/use")
        assert build(c)["status"] == "done"
        persona = c.get("/api/persona").json()
    assert persona["hours_per_week"] == 28 and persona["income_goal_monthly"] == 9000
    assert persona["role"] == "Interim PM" and persona["wants"] == "Rescue work"  # empty extraction didn't wipe
    assert persona["name"] == "Sam Verhoeven"
    assert persona["skills"] == ["PRINCE2", "Budgeting", "Coaching"]  # CV first, interview extras kept


def test_cv_with_nothing_usable_and_no_persona_errors(app):
    fake = FakeAI(PersonaExtraction=[EMPTY_PERSONA], FitProposal=[fit_proposal("x")], OfferSet=[OFFERS])
    with app(fake) as c:
        c.post("/api/sample-cv/use")
        job = build(c)
    assert job["status"] == "error" and "Is it your CV?" in job["error"]
    assert fake.count("FitProposal") == 0


def test_offer_below_floor_is_retried_then_repriced_at_the_ask(app):
    cheap = profile.OfferSet(offers=[o.model_copy(update={"price": "$60/hour"}) if i == 0 else o
                                     for i, o in enumerate(OFFERS.offers)])
    fake = FakeAI(PersonaExtraction=[PERSONA], FitProposal=[lambda i: fit_proposal(cv_source_from(i))],
                  OfferSet=[cheap])
    with app(fake) as c:
        c.post("/api/sample-cv/use")
        build(c)
        offers = c.get("/api/offers").json()["offers"]
    assert fake.count("OfferSet") == 3
    assert offers[0]["price"] == "$120/hour"


def test_goal_checks_are_pure_code():
    allowed = ["data/benchmarks.json#hourly", "data/benchmarks.json#salary_equivalent", "raw/cv.txt"]
    ok = fit_proposal("raw/cv.txt").model_dump()
    assert all(c["passed"] for c in profile.goal_checks(ok, 100, (47, 200), allowed))
    data_prefixed = fit_proposal("data/raw/cv.txt").model_dump()  # same file, written with data/
    assert all(c["passed"] for c in profile.goal_checks(data_prefixed, 100, (47, 200), allowed))
    too_high = fit_proposal("raw/cv.txt", high=251).model_dump()
    failed = [c["name"] for c in profile.goal_checks(too_high, 100, (47, 200), allowed) if not c["passed"]]
    assert failed == ["Range inside the benchmarks"]
    no_source = fit_proposal("raw/cv.txt").model_dump()
    no_source["fits"][0]["sources"] = []
    failed = [c["name"] for c in profile.goal_checks(no_source, 100, (47, 200), allowed) if not c["passed"]]
    assert failed == ["Each fit has a source"]
