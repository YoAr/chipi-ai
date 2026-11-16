"""Lead scoring (app/ai/scoring.py). Never calls OpenAI: `ai.structured` is monkeypatched.

The floor is the approved profile's, in EUR/hour; post rates are converted to EUR for ranking."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import jobs
from app.ai import client as ai_client
from app.ai import scoring
from app.config import REPO_ROOT, Settings

PERSONA = {
    "role": "Project manager",
    "location": "Amsterdam, Netherlands",
    "skills": ["System roll-outs", "Project recovery", "Budgeting", "Stakeholder management", "PRINCE2"],
    "wants": "Interim project management",
    "avoids": "Pure Scrum Master roles; travel of more than a week a month",
}
FIT = {"rate": {"floor": 100, "ask": 120, "currency": "EUR", "unit": "hour"}}


def settings(tmp_path):
    return Settings(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                    cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                    openai_key_set=True, model_smart="m", model_check="m", model_fast="m")


def lead(id, source="remoteok", rate="€120–140/hr", desc="Interim PM for a system roll-out", **kw):
    return {"id": id, "title": kw.pop("title", "Interim project manager"), "company": "Acme",
            "source": source, "url": f"https://example.com/{id}", "fetched": True,
            "rate_text": rate, "description": desc, "score": None, "why": "", "status": "new", **kw}


def fake_model(monkeypatch, scores, calls=None):
    """scores: {batch position: (score, why)}; positions missing from the dict are skipped."""
    def structured(role, s, *, instructions, input, schema, tools=None):
        if calls is not None:
            calls.append({"role": role, "input": input})
        return schema(scores=[{"id": f"p{i}", "score": sc, "why": why} for i, (sc, why) in scores.items()])
    monkeypatch.setattr(scoring.ai, "structured", structured)


# Blend -----------------------------------------------------------------------

def test_final_is_60_40_blend_of_model_and_rules(tmp_path, monkeypatch):
    calls = []
    fake_model(monkeypatch, {0: (80, "Asks for a system roll-out lead")}, calls)
    l = lead("a", desc="System roll-outs, budgeting and stakeholder management for a retailer")
    rules = scoring.rules_score(l, PERSONA, FIT, [])["score"]
    assert rules == 100  # 3 skills, rate at ask, remote board

    [out] = scoring.score_leads([l], PERSONA, FIT, settings(tmp_path), lessons=[])
    assert out["score"] == round(0.6 * 80 + 0.4 * rules) == 88
    assert out["why"] == "Asks for a system roll-out lead"
    assert calls[0]["role"] == "fast"  # one batched fast call


def test_blend_with_partial_rules(tmp_path, monkeypatch):
    fake_model(monkeypatch, {0: (50, "Vague post")})
    l = lead("a", rate=None, desc="Budgeting help", source="hn_whos_hiring")
    # skills 1/3 → 50/3, rate unknown → 15, location unknown → 10
    rules = scoring.rules_score(l, PERSONA, FIT, [])["score"]
    assert rules == pytest.approx(50 / 3 + 15 + 10)
    [out] = scoring.score_leads([l], PERSONA, FIT, settings(tmp_path), lessons=[])
    assert out["score"] == round(0.6 * 50 + 0.4 * rules)


def test_one_call_for_at_most_25_leads(tmp_path, monkeypatch):
    calls = []
    fake_model(monkeypatch, {i: (70, "ok") for i in range(25)}, calls)
    out = scoring.score_leads([lead(f"l{i}") for i in range(30)], PERSONA, FIT, settings(tmp_path), lessons=[])
    assert len(calls) == 1 and len(out) == 25
    assert all(isinstance(l["score"], int) and 0 <= l["score"] <= 100 for l in out)


def test_model_score_is_clamped_and_why_is_one_short_line(tmp_path, monkeypatch):
    fake_model(monkeypatch, {0: (250, "x" * 300)})
    [out] = scoring.score_leads([lead("a")], PERSONA, FIT, settings(tmp_path), lessons=[])
    assert 0 <= out["score"] <= 100
    assert len(out["why"]) <= scoring.WHY_MODEL_MAX


def test_lead_the_model_skipped_is_left_unscored(tmp_path, monkeypatch):
    fake_model(monkeypatch, {0: (70, "ok")})
    out = scoring.score_leads([lead("a"), lead("b")], PERSONA, FIT, settings(tmp_path), lessons=[])
    assert out[0]["score"] is not None
    assert out[1]["score"] is None and out[1]["why"].startswith("Not scored")


# Floor -----------------------------------------------------------------------

@pytest.mark.parametrize("rate", ["$60–85/hr", "$100,000-$150,000/year", "€60/hour", "$90k", "$105/hr",
                                  "€720/day"])
def test_below_floor_is_capped_at_60_and_says_so(tmp_path, monkeypatch, rate):
    fake_model(monkeypatch, {0: (100, "Exactly the roll-out work you want")})
    l = lead("a", rate=rate, desc="System roll-outs, budgeting and stakeholder management")
    [out] = scoring.score_leads([l], PERSONA, FIT, settings(tmp_path), lessons=[])
    assert out["score"] <= 60
    assert out["why"].startswith("Below your €100/h floor")
    assert rate in out["why"]  # quotes the post, doesn't invent a converted number
    assert "Exactly the roll-out work" in out["why"]


def test_range_reaching_the_floor_is_not_capped(tmp_path, monkeypatch):
    fake_model(monkeypatch, {0: (100, "Great fit")})
    l = lead("a", rate="€80–110/hr", desc="System roll-outs, budgeting and stakeholder management")
    [out] = scoring.score_leads([l], PERSONA, FIT, settings(tmp_path), lessons=[])
    assert out["score"] > 60 and "floor" not in out["why"]


def test_post_rates_are_converted_to_eur():
    assert scoring.parse_hourly("€100–120/hour") == (100, 120)
    low, high = scoring.parse_hourly("$100–120/hr")
    assert low == pytest.approx(100 * scoring._FX_TO_EUR["USD"]) and high < 120
    assert scoring.parse_hourly("€800/day") == (100, 100)
    assert scoring.parse_hourly("95 per hour") == (95, 95)  # no currency named: EUR (the Dutch market)


def test_ask_from_an_older_usd_fit_is_converted_for_ranking():
    usd = {"rate": {"floor": 100, "ask": 130, "currency": "USD"}}
    assert scoring._floor_ask(usd) == (100, pytest.approx(130 * scoring._FX_TO_EUR["USD"]))


def test_no_floor_known_means_no_cap(tmp_path, monkeypatch):
    fake_model(monkeypatch, {0: (100, "Great fit")})
    [out] = scoring.score_leads([lead("a", rate="$20/hr")], PERSONA, {"rate": {"floor": None}},
                                settings(tmp_path), lessons=[])
    assert "floor" not in out["why"]


# Model unavailable ---------------------------------------------------------

def test_model_unavailable_returns_leads_unscored(tmp_path, monkeypatch):
    def down(*a, **k):
        raise ai_client.AIUnavailable("OPENAI_API_KEY is not set")
    monkeypatch.setattr(scoring.ai, "structured", down)
    leads = [lead("a"), lead("b", rate="$10/hr")]
    out = scoring.score_leads(leads, PERSONA, FIT, settings(tmp_path))
    assert [l["id"] for l in out] == ["a", "b"]
    assert all(l["score"] is None and l["why"] == "Not scored: model unavailable" for l in out)


# Rules ---------------------------------------------------------------------

def test_location_amsterdam_lead_fits_amsterdam_persona():
    onsite = lead("a", source="scout", location="Amsterdam", remote=False)
    elsewhere = lead("b", source="scout", location="Berlin", remote=False)
    assert scoring.location_signal(onsite, PERSONA) == 1.0
    assert scoring.location_signal(elsewhere, PERSONA) == 0.0
    assert scoring.location_signal(lead("c", source="remotive"), PERSONA) == 1.0
    assert scoring.location_signal(lead("d", source="scout", desc="No location given"), PERSONA) == 0.5


def test_skills_match_singular_and_plural():
    assert scoring.skills_overlap(lead("a", desc="Lead a system roll-out"), PERSONA) == pytest.approx(1 / 3)


def test_avoided_work_is_penalised():
    good = lead("a", desc="Project recovery for a logistics firm")
    scrum = lead("b", desc="Scrum Master for two teams, project recovery", title="Scrum Master")
    travel = lead("c", desc="Project recovery; travel two weeks a month to client sites")
    hours = lead("d", desc="Project recovery, 3 month contract, 20 hours a week")
    assert scoring.avoids_hit(good, PERSONA) is None
    assert scoring.avoids_hit(scrum, PERSONA)
    assert scoring.avoids_hit(travel, PERSONA)
    assert scoring.avoids_hit(hours, PERSONA) is None  # "week" and "month" alone aren't travel
    gap = scoring.rules_score(good, PERSONA, FIT)["score"] - scoring.rules_score(scrum, PERSONA, FIT)["score"]
    assert gap == pytest.approx(scoring.WEIGHTS["avoid_penalty"])


def test_weights_are_tuned():
    assert all(v > 0 for v in scoring.WEIGHTS.values())
    assert scoring.WEIGHTS["skills_overlap"] + scoring.WEIGHTS["rate_fit"] + scoring.WEIGHTS["remote"] == 100


# Lessons -------------------------------------------------------------------

LESSONS = scoring.parse_lessons([{"text": "HN Who's Hiring replies more than RemoteOK", "status": "proposed"}])


def test_parse_lessons_maps_channel_names():
    assert LESSONS == [("hn", "remoteok")]
    assert scoring.parse_lessons([{"text": "Direct email replies more than Web search"}]) == [("email", "web_search")]
    assert scoring.parse_lessons([{"text": "Carrier pigeon replies more than RemoteOK"}]) == []


def test_lesson_bonus_and_penalty_applied_and_mentioned(tmp_path, monkeypatch):
    fake_model(monkeypatch, {0: (70, "Roll-out work"), 1: (70, "Roll-out work")})
    hn = lead("h", source="hn_whos_hiring", remote=True)
    rok = lead("r", source="remoteok")
    base = scoring.score_leads([hn, rok], PERSONA, FIT, settings(tmp_path), lessons=[])
    out = scoring.score_leads([hn, rok], PERSONA, FIT, settings(tmp_path), lessons=LESSONS)

    bonus = scoring.rules_score(hn, PERSONA, FIT, LESSONS)["score"] - base_rules(hn)
    assert bonus == pytest.approx(scoring.WEIGHTS["lesson_bonus"])
    assert out[0]["score"] > base[0]["score"]
    assert out[0]["score"] == round(0.6 * 70 + 0.4 * (base_rules(hn) + scoring.WEIGHTS["lesson_bonus"]))
    assert out[0]["why"].startswith("Lean on HN: replies more than RemoteOK")
    assert out[1]["score"] < base[1]["score"]
    assert out[1]["why"].startswith("Go easy on RemoteOK: HN replies more")
    assert out[1]["score"] == round(0.6 * 70 + 0.4 * (base_rules(rok) - scoring.WEIGHTS["lesson_penalty"]))


def base_rules(l):
    return scoring.rules_score(l, PERSONA, FIT, [])["score"]


def test_direct_email_lesson_favours_posts_with_an_email_address():
    lessons = scoring.parse_lessons([{"text": "Direct email replies more than RemoteOK"}])
    with_email = lead("a", source="remotive", desc="Send your CV to jobs@acme.example")
    plain = lead("b", source="remotive")
    assert scoring.lesson_signal(with_email, lessons)[0] == scoring.WEIGHTS["lesson_bonus"]
    assert scoring.lesson_signal(plain, lessons) == (0.0, None)
    # a RemoteOK post with an email address is on both channels: no net nudge
    assert scoring.lesson_signal(lead("c", desc="Mail hr@acme.example"), lessons) == (0.0, None)


def test_unknown_source_gets_no_lesson_adjustment(tmp_path, monkeypatch):
    fake_model(monkeypatch, {0: (70, "Roll-out work in Amsterdam")})
    scout = lead("s", source="scout", location="Amsterdam", desc="Email me at a@b.example")
    lessons = LESSONS + [("email", "remoteok"), ("remoteok", "hn")]
    assert scoring.lead_channels(scout) == set()
    assert scoring.rules_score(scout, PERSONA, FIT, lessons) == scoring.rules_score(scout, PERSONA, FIT, [])
    [out] = scoring.score_leads([scout], PERSONA, FIT, settings(tmp_path), lessons=lessons)
    assert out["why"] == "Roll-out work in Amsterdam"


def _write_events(data_dir, wins: dict[str, tuple[int, int]]):
    """draft_approved/reply_received events, 20 days old, per channel: (sent, replies)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    ts = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines, n = [], 0
    for ch, (sent, replies) in wins.items():
        for k in range(sent):
            n += 1
            lines.append({"id": f"ev_{n:04d}", "ts": ts, "type": "draft_approved", "actor": "user",
                          "subject": f"draft:{n}", "payload": {"channel": ch, "lead": f"{ch}-{k}"}})
            if k < replies:
                n += 1
                lines.append({"id": f"ev_{n:04d}", "ts": ts, "type": "reply_received", "actor": "world",
                              "subject": f"lead:{k}", "payload": {"channel": ch, "lead": f"{ch}-{k}"}})
    # metrics measure "now" as the latest event, so add a recent one: the sends above are settled
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append({"id": f"ev_{n + 1:04d}", "ts": now, "type": "run_completed", "actor": "agent",
                  "subject": "run:1", "payload": {}})
    (data_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))


def test_current_lessons_read_from_the_events_log(tmp_path):
    s = settings(tmp_path)
    assert scoring.current_lessons(s) == []  # no log yet
    _write_events(s.data_dir, {"HN Who's Hiring": (12, 10), "RemoteOK": (12, 1)})
    assert scoring.current_lessons(s) == [("hn", "remoteok")]


# run_search ----------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self.docs = {"persona": PERSONA, "fit": FIT}
        self.saved = []
        self.jobs = {}

    def get(self, name):
        return self.docs[name]

    def get_setting(self, key, default=None):
        return self.jobs.get(key, default)

    def set_setting(self, key, value):
        self.jobs[key] = value

    def upsert_leads(self, leads):
        self.saved = leads
        return leads


def test_run_search_ranks_applies_lessons_and_saves(tmp_path, monkeypatch):
    from app.sources import hn, remoteok, remotive

    monkeypatch.setattr(scoring.profiles, "rate_floor", lambda store: 100.0)  # the approved profile's floor
    s = settings(tmp_path)
    _write_events(s.data_dir, {"HN Who's Hiring": (12, 10), "RemoteOK": (12, 1)})
    weak = [lead(f"w{i}", source="remotive", rate="$20/hr", desc="Data entry") for i in range(25)]
    strong = [lead("best", source="hn_whos_hiring", remote=True,
                   desc="System roll-outs, budgeting and stakeholder management")]
    monkeypatch.setattr(remotive, "fetch", lambda c, w, limit=30: weak)
    monkeypatch.setattr(remoteok, "fetch", lambda c, w, limit=30: [])
    monkeypatch.setattr(hn, "fetch", lambda c, w, limit=30: strong)  # fetched last: would miss the first 25
    calls = []
    fake_model(monkeypatch, {i: (60, "ok") for i in range(25)}, calls)

    store = FakeStore()
    jobs.begin(store, scoring.JOB, step="fetching", message="")
    scoring.run_search(store, s)

    assert len(calls) == 1
    assert len(store.saved) == 25
    assert store.saved[0]["id"] == "best"  # ranked into the batch and to the top
    assert store.saved[0]["why"].startswith("Lean on HN")
    scores = [l["score"] for l in store.saved]
    assert scores == sorted(scores, reverse=True)
    assert jobs.get(store, scoring.JOB)["status"] == "done"


def _run_search_with_floor(tmp_path, monkeypatch, approved_floor, fit):
    from app.sources import hn, remoteok, remotive

    monkeypatch.setattr(scoring.profiles, "rate_floor", lambda store: approved_floor)
    monkeypatch.setattr(remotive, "fetch", lambda c, w, limit=30: [lead("cheap", source="remotive", rate="€90/hour")])
    monkeypatch.setattr(remoteok, "fetch", lambda c, w, limit=30: [])
    monkeypatch.setattr(hn, "fetch", lambda c, w, limit=30: [])
    fake_model(monkeypatch, {0: (90, "Roll-out work")})
    store = FakeStore()
    store.docs["fit"] = fit
    jobs.begin(store, scoring.JOB, step="fetching", message="")
    scoring.run_search(store, settings(tmp_path))
    return store.saved[0]


def test_run_search_uses_the_approved_floor_not_the_fits(tmp_path, monkeypatch):
    stale = {"rate": {"floor": 50, "ask": 120, "currency": "EUR"}}
    out = _run_search_with_floor(tmp_path, monkeypatch, 95.0, stale)
    assert out["why"].startswith("Below your €95/h floor") and out["score"] <= 60


def test_run_search_without_an_approved_floor_skips_the_floor_rule(tmp_path, monkeypatch):
    out = _run_search_with_floor(tmp_path, monkeypatch, None, FIT)
    assert "floor" not in out["why"] and out["score"] is not None
