"""Drafts AI: writer + verifier (app/ai/drafts.py). Never calls OpenAI: `ai.structured` is faked.

Money is EUR per hour; the floor is the approved profile's (approved at €100/hour in the fixtures)."""

import pytest
from fastapi.testclient import TestClient

from app import openai_check
from app.ai import drafts
from app.config import REPO_ROOT, Settings
from app.main import create_app

FIT = {
    "fits": [{"name": "Backend + AI integration contracts", "level": "high",
              "reason": "Python APIs and LLM agent work", "sources": ["raw/cv.md"]}],
    "rate": {"floor": 100, "ask": 120, "low": 110, "high": 140, "currency": "EUR", "unit": "hour", "reasons": []},
    "goal_checks": [],
}
PERSONA = {"name": "Sam Rivera", "role": "Backend engineer", "years": 5, "location": "Amsterdam",
           "skills": ["Python", "FastAPI", "LLM agents"]}
LEAD = {"title": "Backend engineer, payments API (contract)", "company": "Acme",
        "url": "https://example.com/jobs/payments", "source": "remotive", "rate_text": "€120-140/hr",
        "score": 90, "why": "Python + FastAPI", "fetched": True}

GOOD_BODY = ("Hi [NAME],\n\nI saw your payments API post. I have 5 years of Python and FastAPI backend work: "
             "[ONE RELEVANT PROJECT].\n\nI'm available [HOURS]/week at €120/hour. "
             "Could we do a 20-minute call this week?\n\nSam")
WRONG_RATE_BODY = GOOD_BODY.replace("€120/hour", "€95/hour")
DOLLAR_BODY = GOOD_BODY.replace("€120/hour", "$120/hour")


def settings(tmp_path):
    return Settings(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    openai_key_set=True, model_smart="smart-m", model_check="check-m", model_fast="fast-m")


class FakeAI:
    """Stands in for ai.structured: hands out queued writer bodies; the checker passes unless told not to."""

    def __init__(self, bodies, claims_backed=True, tone=True):
        self.bodies = list(bodies)
        self.claims_backed, self.tone = claims_backed, tone
        self.calls = []

    def __call__(self, role, settings, *, instructions, input, schema, tools=None):
        self.calls.append({"role": role, "input": input, "schema": schema})
        if schema is drafts.DraftText:
            return drafts.DraftText(subject="Payments API contract", body=self.bodies.pop(0))
        return drafts.DraftReview(claims_backed=self.claims_backed,
                                  unsupported_claims=[] if self.claims_backed else ["led a team of 40"],
                                  claims_note="" if self.claims_backed else "One claim isn't in your CV",
                                  professional_tone=self.tone, tone_note="")

    def roles(self):
        return [c["role"] for c in self.calls]


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    # The startup model check would list OpenAI models over the network; keep tests offline.
    monkeypatch.setattr(openai_check, "_list_model_ids", lambda: [])
    with TestClient(create_app(settings(tmp_path))) as c:
        yield c


@pytest.fixture()
def store(app_client, approve_profile):
    approve_profile(app_client, 100)  # the approved profile owns the €100/hour floor
    return app_client.app.state.store


@pytest.fixture()
def lead_id(store):
    store.set_doc("fit", FIT)
    store.update_persona(PERSONA)
    return store.upsert_leads([LEAD])[0]["id"]


def checks_by_name(draft):
    return {c["name"]: c for c in draft["verifier"]["checks"]}


# Through the route -------------------------------------------------------------


def test_happy_path_through_the_route(app_client, store, lead_id, monkeypatch):
    fake = FakeAI([GOOD_BODY])
    monkeypatch.setattr(drafts.ai, "structured", fake)

    assert app_client.post(f"/api/leads/{lead_id}/draft").status_code == 202
    job = app_client.get(f"/api/leads/{lead_id}/draft/status").json()
    assert job["status"] == "done", job
    draft_id = job["result"]["draft_id"]

    pending = app_client.get("/api/drafts", params={"status": "pending"}).json()["drafts"]
    draft = next(d for d in pending if d["id"] == draft_id)
    assert draft["lead_id"] == lead_id and draft["status"] == "pending"
    assert draft["channel"] == "apply_link" and draft["to"] == LEAD["url"]
    assert draft["body"] == GOOD_BODY
    assert draft["verifier"]["passed"] is True
    # Shape the UI's <Checks> renders: unique names, {name, passed, note}
    names = [c["name"] for c in draft["verifier"]["checks"]]
    assert len(names) == len(set(names)) == 6
    assert all(set(c) == {"name", "passed", "note"} for c in draft["verifier"]["checks"])
    # Writer and checker are separate calls on separate models; no rewrite needed
    assert fake.roles() == ["smart", "check"]
    assert any(x["id"] == lead_id and x["status"] == "drafted" for x in store.leads()["leads"])


def test_wrong_rate_fails_the_check_and_gets_one_rewrite(app_client, store, lead_id, monkeypatch):
    fake = FakeAI([WRONG_RATE_BODY, GOOD_BODY])
    monkeypatch.setattr(drafts.ai, "structured", fake)

    app_client.post(f"/api/leads/{lead_id}/draft")
    job = app_client.get(f"/api/leads/{lead_id}/draft/status").json()
    assert job["status"] == "done"
    assert fake.roles() == ["smart", "smart", "check"]
    rewrite_input = fake.calls[1]["input"]
    assert "Rate matches your ask" in rewrite_input and "€95" in rewrite_input
    assert "€120/hour" in fake.calls[0]["input"]  # the writer is told to quote the ask in euros
    draft = store.drafts()["drafts"][0]
    assert draft["body"] == GOOD_BODY and draft["verifier"]["passed"] is True


def test_still_failing_draft_is_saved_with_passed_false(app_client, store, lead_id, monkeypatch):
    fake = FakeAI([WRONG_RATE_BODY, WRONG_RATE_BODY])
    monkeypatch.setattr(drafts.ai, "structured", fake)

    app_client.post(f"/api/leads/{lead_id}/draft")
    job = app_client.get(f"/api/leads/{lead_id}/draft/status").json()
    assert job["status"] == "done" and job["result"]["draft_id"]
    assert fake.roles() == ["smart", "smart", "check"]  # one rewrite only
    draft = store.drafts()["drafts"][0]
    assert draft["id"] == job["result"]["draft_id"]
    assert draft["verifier"]["passed"] is False
    checks = checks_by_name(draft)
    assert checks["Rate matches your ask"]["passed"] is False
    assert checks["Not below your floor"]["passed"] is False
    assert checks["Not below your floor"]["note"] == "€95 is below your floor of €100/hour"


def test_no_fit_gives_a_clear_error(app_client, store, monkeypatch):
    lid = store.upsert_leads([LEAD])[0]["id"]
    fake = FakeAI([GOOD_BODY])
    monkeypatch.setattr(drafts.ai, "structured", fake)

    app_client.post(f"/api/leads/{lid}/draft")
    job = app_client.get(f"/api/leads/{lid}/draft/status").json()
    assert job["status"] == "error" and job["error"] == "Build your profile first"
    assert fake.calls == [] and store.drafts()["drafts"] == []


def test_no_approved_profile_gives_a_clear_error(app_client, monkeypatch):
    store = app_client.app.state.store
    store.update_persona(PERSONA)
    store.set_doc("fit", {**FIT, "rate": {**FIT["rate"], "floor": None}})
    lid = store.upsert_leads([LEAD])[0]["id"]
    fake = FakeAI([GOOD_BODY])
    monkeypatch.setattr(drafts.ai, "structured", fake)

    app_client.post(f"/api/leads/{lid}/draft")
    job = app_client.get(f"/api/leads/{lid}/draft/status").json()
    assert job["status"] == "error"
    assert job["error"] == "Approve your profile first: it sets your minimum hourly rate."
    assert fake.calls == [] and store.drafts()["drafts"] == []


def test_approved_floor_wins_over_the_fits_floor(app_client, store, approve_profile, monkeypatch):
    approve_profile(app_client, 130)  # a newer approved profile raises the floor
    store.set_doc("fit", FIT)  # an older fit that still says €100
    lid = store.upsert_leads([LEAD])[0]["id"]
    monkeypatch.setattr(drafts.ai, "structured", FakeAI([GOOD_BODY, GOOD_BODY]))
    app_client.post(f"/api/leads/{lid}/draft")
    floor_check = checks_by_name(store.drafts()["drafts"][0])["Not below your floor"]
    assert floor_check["passed"] is False and floor_check["note"] == "€120 is below your floor of €130/hour"


def test_dollar_amount_fails_the_rate_check(app_client, store, lead_id, monkeypatch):
    fake = FakeAI([DOLLAR_BODY, GOOD_BODY])
    monkeypatch.setattr(drafts.ai, "structured", fake)
    app_client.post(f"/api/leads/{lead_id}/draft")
    assert fake.roles() == ["smart", "smart", "check"]
    assert "$120" in fake.calls[1]["input"] and "quote in euros" in fake.calls[1]["input"]
    assert store.drafts()["drafts"][0]["verifier"]["passed"] is True


def test_unsupported_claim_fails_the_model_check(app_client, store, lead_id, monkeypatch):
    monkeypatch.setattr(drafts.ai, "structured", FakeAI([GOOD_BODY], claims_backed=False))
    app_client.post(f"/api/leads/{lead_id}/draft")
    draft = store.drafts()["drafts"][0]
    claims = checks_by_name(draft)["Claims backed by your CV"]
    assert draft["verifier"]["passed"] is False
    assert claims["passed"] is False and "led a team of 40" in claims["note"]


# Units ---------------------------------------------------------------------------


def test_no_fit_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts.ai, "structured", FakeAI([GOOD_BODY]))
    for fit in (None, {}, {"fits": [], "rate": {"ask": None}}):
        with pytest.raises(ValueError, match="Build your profile first"):
            drafts.write_draft(LEAD, PERSONA, fit, settings(tmp_path))


def test_channel_email_when_the_post_has_an_address(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts.ai, "structured", FakeAI([GOOD_BODY]))
    lead = {**LEAD, "description": "Send your CV to Jobs@Acme.example.com. Remote OK."}
    d = drafts.write_draft(lead, PERSONA, FIT, settings(tmp_path))
    assert d["channel"] == "email" and d["to"] == "Jobs@Acme.example.com"

    on_list = drafts.code_checks(d, lead, PERSONA, FIT, 100, allowlist=["jobs@acme.example.com"])
    assert {c["name"]: c["passed"] for c in on_list}["Channel allowed"] is True
    off_list = {c["name"]: c for c in drafts.code_checks(d, lead, PERSONA, FIT, 100, allowlist=["me@x.com"])}
    assert off_list["Channel allowed"]["passed"] is False and "allowlist" in off_list["Channel allowed"]["note"]


def test_channel_apply_link_otherwise(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts.ai, "structured", FakeAI([GOOD_BODY]))
    d = drafts.write_draft({**LEAD, "description": "Apply on our site."}, PERSONA, FIT, settings(tmp_path))
    assert d["channel"] == "apply_link" and d["to"] == LEAD["url"]
    checks = {c["name"]: c["passed"] for c in drafts.code_checks(d, LEAD, PERSONA, FIT, 100)}
    assert checks["Channel allowed"] is True


def test_email_channel_through_run_draft_uses_the_allowlist(app_client, store, lead_id, monkeypatch):
    lead = next(x for x in store.leads()["leads"] if x["id"] == lead_id)
    monkeypatch.setattr(drafts.legacy_drafts, "_find_lead", lambda s, i: {**lead, "description": "Email hiring@acme.example.com"})
    monkeypatch.setenv("EMAIL_ALLOWLIST", "hiring@acme.example.com, other@x.com")
    fake = FakeAI([GOOD_BODY])
    monkeypatch.setattr(drafts.ai, "structured", fake)

    app_client.post(f"/api/leads/{lead_id}/draft")
    draft = store.drafts()["drafts"][0]
    assert draft["channel"] == "email" and draft["to"] == "hiring@acme.example.com"
    assert draft["verifier"]["passed"] is True
    assert fake.roles() == ["smart", "check"]  # channel is set in code; nothing to rewrite


def test_code_checks_length_and_one_ask():
    long_body = ("word " * 160) + "Could we talk? €120/hour"
    checks = {c["name"]: c for c in drafts.code_checks(
        {"subject": "s", "body": long_body, "channel": "apply_link", "to": LEAD["url"]}, LEAD, PERSONA, FIT, 100)}
    assert checks["Under 150 words, one ask"]["passed"] is False
    assert "words" in checks["Under 150 words, one ask"]["note"]

    two_asks = GOOD_BODY.replace("Hi [NAME],", "Hi [NAME], are you still hiring?")
    checks = {c["name"]: c for c in drafts.code_checks(
        {"subject": "s", "body": two_asks, "channel": "apply_link", "to": LEAD["url"]}, LEAD, PERSONA, FIT, 100)}
    assert checks["Under 150 words, one ask"]["passed"] is False
    assert checks["Rate matches your ask"]["passed"] is True


def test_missing_rate_fails_and_ask_below_floor_fails():
    no_rate = GOOD_BODY.replace(" at €120/hour", "")
    checks = {c["name"]: c for c in drafts.code_checks(
        {"subject": "s", "body": no_rate, "channel": "apply_link", "to": LEAD["url"]}, LEAD, PERSONA, FIT, 100)}
    assert checks["Rate matches your ask"]["passed"] is False

    low_fit = {**FIT, "rate": {**FIT["rate"], "ask": 90}}
    body = GOOD_BODY.replace("€120", "€90")
    checks = {c["name"]: c for c in drafts.code_checks(
        {"subject": "s", "body": body, "channel": "apply_link", "to": LEAD["url"]}, LEAD, PERSONA, low_fit, 100)}
    assert checks["Rate matches your ask"]["passed"] is True
    assert checks["Not below your floor"]["passed"] is False


def test_model_check_is_a_fresh_check_model_call_with_the_cv(tmp_path, monkeypatch):
    fake = FakeAI([])
    monkeypatch.setattr(drafts.ai, "structured", fake)
    d = {"subject": "s", "body": GOOD_BODY, "channel": "apply_link", "to": LEAD["url"]}
    out = drafts.model_check(d, LEAD, PERSONA, settings(tmp_path), cv_text="Five years of FastAPI at Initech")
    assert [c["name"] for c in out] == ["Claims backed by your CV", "Professional tone"]
    assert fake.roles() == ["check"] and "Initech" in fake.calls[0]["input"]
    assert "rate_to_quote" not in fake.calls[0]["input"]  # nothing from the writer's prompt


def test_money_in_euros_and_other_currencies_is_read():
    d = {"subject": "s", "channel": "apply_link", "to": LEAD["url"]}
    eur_word = {c["name"]: c for c in drafts.code_checks(
        {**d, "body": GOOD_BODY.replace("€120/hour", "EUR 120 per hour")}, LEAD, PERSONA, FIT, 100)}
    assert eur_word["Rate matches your ask"]["passed"] is True
    dollars = {c["name"]: c for c in drafts.code_checks({**d, "body": DOLLAR_BODY}, LEAD, PERSONA, FIT, 100)}
    assert dollars["Rate matches your ask"]["passed"] is False
    assert dollars["Rate matches your ask"]["note"] == "Quotes $120; your ask is €120/hour (quote in euros)"
    assert dollars["Not below your floor"]["passed"] is True  # the ask itself is fine; the currency is not


def test_fit_in_another_currency_is_not_quoted(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts.ai, "structured", FakeAI([GOOD_BODY]))
    usd_fit = {**FIT, "rate": {**FIT["rate"], "currency": "USD"}}
    with pytest.raises(ValueError, match="rebuild your profile"):
        drafts.write_draft(LEAD, PERSONA, usd_fit, settings(tmp_path))
