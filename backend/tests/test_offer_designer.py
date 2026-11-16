"""Demand index (spec v2 §6.2) and Offer designer (§5.2). Fake model, fictional panel; no network."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import demand
from app.ai import interviewer, offer_designer
from app.ai.offer_designer import DiscoveryTerms, RateRange, Service, Services
from app.compliance import find_banned
from app.config import REPO_ROOT, Settings
from app.guards import RateLimiter
from app.main import create_app
from app.storage import companies

NOW = datetime.now(timezone.utc)


def day(n_ago: int) -> str:
    return (NOW - timedelta(days=n_ago)).strftime("%Y-%m-%d")


def seed_panel(conn, postings, d=None):
    """postings: [(skills, employment_type)] -> today's snapshot."""
    rows = [{"source_posting_id": f"recruitee:{i}", "title": "x", "employment_type": et, "skills": skills,
             "published_at": None, "language": "en"} for i, (skills, et) in enumerate(postings)]
    companies.save_snapshot(conn, d or day(0), "recruitee", "acme", rows)


PROFILE = {
    "skills": [
        {"name": "Python", "taxonomy_id": None, "proof_strength": "referenceable",
         "evidence": [{"claim": "Built pipelines", "metric": None, "client": "Tulip Data", "citable": True}]},
        {"name": "dbt", "taxonomy_id": None, "proof_strength": "demonstrated",
         "evidence": [{"claim": "Rebuilt models", "metric": "40%", "client": None, "citable": False}]},
        {"name": "Airflow", "taxonomy_id": None, "proof_strength": "claimed", "evidence": []},
        {"name": "Pottery", "taxonomy_id": None, "proof_strength": "claimed", "evidence": []},
    ],
    "skill_combinations": [{"skills": ["Python", "dbt"], "evidence": "Rebuilt the dbt models with Python tests"}],
    "industries": ["fintech"], "preferred_work": ["data platform rebuilds"], "exclusions": ["CMS maintenance"],
    "rate_floor_eur_hour": 95, "availability_hours_week": 24, "past_employers": ["Tulip Data"], "schools": [],
    "languages": ["nl", "en"], "voice_samples": [],
}


# Demand index -------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path):
    from app.storage import RealStore
    s = RealStore(tmp_path / "data", REPO_ROOT / "data")
    yield s
    s.close()


def test_empty_panel_gives_no_index_and_says_why(store):
    out = demand.service_index(store.conn, ["Python", "dbt"], "referenceable")
    assert out["value"] is None and "panel hasn't been snapshotted" in out["explanation"]


def test_pairs_beat_singles_when_they_cooccur_and_proof_scales(store):
    seed_panel(store.conn, [(["Python", "dbt"], "freelance")] * 6 + [(["Python"], "fulltime_permanent")] * 10
               + [(["Java"], "fulltime_permanent")] * 20)
    ref = demand.service_index(store.conn, ["Python", "dbt"], "referenceable")
    dem = demand.service_index(store.conn, ["Python", "dbt"], "demonstrated")
    assert ref["best"]["skills"] == ["Python", "dbt"] and ref["best"]["count"] == 6  # pairs, not singles
    assert 0 < ref["value"] <= 1 and dem["value"] == pytest.approx(ref["value"] * 0.8, abs=0.002)
    pair = demand.posting_signal(demand.load_panel(store.conn), [{"Python"}, {"dbt"}])
    assert pair["lift"] > 1 and pair["premium"] > 0 and pair["contract_share"] == 1.0
    assert pair["trend"] is None  # one snapshot day: not enough history
    assert "not enough history" in ref["explanation"] and "not total market demand" in ref["explanation"]


def test_unknown_skills_are_reported_not_guessed(store):
    seed_panel(store.conn, [(["Python"], "fulltime_permanent")] * 3)
    out = demand.service_index(store.conn, ["Pottery", "Python"], "demonstrated")
    assert out["value"] is None and out["no_market_data"] == ["Pottery"]
    assert "Needs two skills" in out["explanation"] and "No market tag for: Pottery" in out["explanation"]


def test_fixed_term_employment_is_not_contract_work_and_scores_spread(store):
    seed_panel(store.conn, [(["Python", "dbt"], "fulltime_fixed_term")] * 2 + [(["Python", "SQL"], "contract")] * 4
               + [(["Java"], "fulltime_permanent")] * 194)
    panel = demand.load_panel(store.conn)
    a = demand.posting_signal(panel, [{"Python"}, {"dbt"}])
    b = demand.posting_signal(panel, [{"Python"}, {"SQL"}])
    assert a["contract_share"] == 0 and b["contract_share"] == 1.0
    assert 0 < a["signal"] < b["signal"] < 1  # 1% vs 2% of postings: different, neither saturated


def test_trend_needs_history_and_follows_recent_share(store):
    seed_panel(store.conn, [(["Java"], "x")] * 10, d=day(80))
    rows = [{"source_posting_id": f"recruitee:new{i}", "title": "x", "employment_type": "x", "skills": ["Python"],
             "published_at": None, "language": "en"} for i in range(10)]
    companies.save_snapshot(store.conn, day(1), "recruitee", "acme", rows)
    sig = demand.posting_signal(demand.load_panel(store.conn), [{"Python"}])
    assert sig["trend"] == 1.0  # all Python postings are recent


def test_revealed_demand_weight_grows_with_sample_size():
    assert demand.weights(0)["revealed"] == 0 and demand.weights(0)["posting"] == 1.0  # sector unverified
    assert demand.weights(20)["revealed"] == 0.5
    assert demand.weights(80)["revealed"] > 0.75


# Compliance ---------------------------------------------------------------------
def test_wet_dba_phrases():
    assert find_banned("Fixed-scope dbt migration in 6 weeks") == []
    assert find_banned("I can join your team 40 hours a week")
    assert find_banned("Versterking van uw team, 32 uur per week")
    assert not find_banned("A fulltimer-free plan")  # word boundaries


# Offer designer -----------------------------------------------------------------
def svc(id, skills, lo=110, hi=130, deliverable="A migrated dbt project with tests and a runbook", weeks=6,
        name=None, rationale="Strong Python + dbt demand in the panel.", nl=("data engineer", "dbt specialist"),
        en=("data engineer", "analytics engineer")):
    return Service(id=id, name=name or f"Service {id}", target_buyer="Scale-ups with a data team of 1-3",
                   problem="Slow nightly loads", deliverable=deliverable, duration_weeks=weeks, skills_used=list(skills),
                   rate_range_eur=RateRange(min=lo, max=hi), demand_index=0, proof_strength="claimed",
                   independence_rationale="Fixed scope, own tools, delivered to several clients",
                   rationale=rationale, discovery_terms=DiscoveryTerms(nl=list(nl), en=list(en)))


GOOD = Services(services=[svc("a", ["Python", "dbt"]), svc("b", ["dbt", "Airflow"]), svc("c", ["Python", "Airflow"])])


class FakeModel:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, role, settings, *, instructions, input, schema, tools=None):
        self.calls.append({"role": role, "input": input, "instructions": instructions})
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def settings(tmp_path):
    return Settings(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    openai_key_set=True, model_smart="smart-model", model_check="m", model_fast="m")


@pytest.fixture()
def client(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.action_limiter = RateLimiter(max_calls=1000, per_seconds=60)
    with TestClient(app) as c:
        c.app_ref = app
        yield c


def approve_profile(c, profile=PROFILE):
    v = c.put("/api/profile", json=profile).json()
    assert c.post("/api/profile/approve", json={"version": v["version"]}).status_code == 200


def test_needs_an_approved_profile(client, monkeypatch):
    monkeypatch.setattr(offer_designer.ai, "structured", FakeModel([GOOD]))
    client.post("/api/services/build")
    job = client.get("/api/services/status").json()
    assert job["status"] == "error" and "Approve your profile" in job["error"]


def test_good_services_get_code_computed_demand_and_proof(client, monkeypatch):
    approve_profile(client)
    seed_panel(client.app_ref.state.store.conn, [(["Python", "dbt"], "freelance")] * 5 + [(["Java"], "x")] * 5)
    fake = FakeModel([GOOD])
    monkeypatch.setattr(offer_designer.ai, "structured", fake)
    client.post("/api/services/build")
    assert client.get("/api/services/status").json()["status"] == "done"
    assert len(fake.calls) == 1 and fake.calls[0]["role"] == "smart"
    assert "hiring_demand_inputs" in fake.calls[0]["input"] and "€95/hour" in fake.calls[0]["instructions"]

    latest = client.get("/api/services").json()["latest"]
    services = {s["id"]: s for s in latest["data"]["services"]}
    a = services["a"]
    assert a["proof_strength"] == "referenceable"  # strongest of Python (ref) and dbt (dem), from the profile
    assert a["demand_index"] > 0 and a["demand"]["best"]["skills"] in (["Python", "dbt"], ["Python"], ["dbt"])
    assert "panel postings" in a["demand"]["explanation"]
    assert services["b"]["proof_strength"] == "demonstrated"
    assert latest["provenance"]["prompt_version"] == offer_designer.PROMPT_VERSION
    ordered = [s["demand_index"] for s in latest["data"]["services"]]
    assert ordered == sorted(ordered, reverse=True)


def test_unsafe_services_are_retried_then_dropped(client, monkeypatch):
    approve_profile(client)
    bad = Services(services=[
        svc("a", ["Python", "dbt"]),
        svc("emp", ["Python", "dbt"], name="Join your team as a data engineer, 40 hours a week"),
        svc("single", ["Python"]),
        svc("unproven", ["Airflow", "Pottery"]),
        svc("invented", ["Python", "Kubernetes"]),
    ])
    retry = Services(services=[svc("a", ["Python", "dbt"]), svc("b", ["dbt", "Airflow"]), svc("c", ["Python", "Airflow"]),
                               svc("cms", ["Python", "dbt"], deliverable="CMS maintenance retainer")])
    fake = FakeModel([bad, retry])
    monkeypatch.setattr(offer_designer.ai, "structured", fake)
    client.post("/api/services/build")
    assert len(fake.calls) == 2
    fb = fake.calls[1]["input"]
    assert "Wet DBA risk phrases" in fb and "single skill" in fb and "no demonstrated or referenceable" in fb
    assert "not in the approved profile: Kubernetes" in fb
    latest = client.get("/api/services").json()["latest"]
    assert [s["id"] for s in latest["data"]["services"]] == ["a", "b", "c"] or \
        sorted(s["id"] for s in latest["data"]["services"]) == ["a", "b", "c"]
    dropped = next(c for c in latest["checks"] if c["name"].startswith("Skills from your approved profile"))
    assert not dropped["passed"] and "excluded work: CMS maintenance" in dropped["note"]


def test_below_floor_rates_are_raised_to_the_floor(client, monkeypatch):
    approve_profile(client)
    low = Services(services=[svc("a", ["Python", "dbt"], lo=60, hi=80), svc("b", ["dbt", "Airflow"]),
                             svc("c", ["Python", "Airflow"])])
    monkeypatch.setattr(offer_designer.ai, "structured", FakeModel([low, low]))
    client.post("/api/services/build")
    s = {x["id"]: x for x in client.get("/api/services").json()["latest"]["data"]["services"]}
    assert s["a"]["rate_range_eur"] == {"min": 95, "max": 95}


def test_fewer_than_three_safe_services_fails_with_reasons(client, monkeypatch):
    approve_profile(client)
    bad = Services(services=[svc("a", ["Python", "dbt"]), svc("x", ["Python"]), svc("y", ["Pottery", "Airflow"])])
    monkeypatch.setattr(offer_designer.ai, "structured", FakeModel([bad, bad]))
    client.post("/api/services/build")
    job = client.get("/api/services/status").json()
    assert job["status"] == "error" and "Couldn't design 3 services" in job["error"]


def test_approval_hands_discovery_terms_to_scout_and_floor_is_enforced(client, monkeypatch):
    approve_profile(client)
    monkeypatch.setattr(offer_designer.ai, "structured", FakeModel([GOOD]))
    client.post("/api/services/build")
    latest = client.get("/api/services").json()["latest"]

    # The freelancer's own edit below the floor is refused (rule 2); Wet DBA wording is only flagged
    edited = latest["data"]["services"]
    too_low = [{**edited[0], "rate_range_eur": {"min": 50, "max": 60}}] + edited[1:]
    assert client.put("/api/services", json={"services": too_low}).status_code == 409
    flagged = [{**edited[0], "deliverable": "Ongoing support, 40 hours a week"}] + edited[1:]
    v = client.put("/api/services", json={"services": flagged}).json()
    assert v["author"] == "user" and not v["checks"][0]["passed"]

    ok = client.post("/api/services/approve", json={"version": v["version"]}).json()
    assert ok["status"] == "approved"
    store = client.app_ref.state.store
    terms = store.get_setting("discovery_terms")
    assert terms["nl"] == ["data engineer", "dbt specialist"] and "analytics engineer" in terms["en"]
    from app import scout
    # NL and EN interleaved (so a cap still searches both), duplicates dropped
    assert scout.default_terms(store) == ["data engineer", "dbt specialist", "analytics engineer"]
    assert '"services_approved"' in (store.data_dir / "events.jsonl").read_text()
