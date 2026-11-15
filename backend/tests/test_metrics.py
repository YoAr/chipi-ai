import json
from pathlib import Path

from app.metrics import compute_metrics, p_better, rate_with_range
from app.simulate import simulate

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "metrics.json").read_text())


def test_rate_zero_trials():
    v, lo, hi = rate_with_range(0, 0)
    assert v == 0.5
    assert lo < 0.15 and hi > 0.85


def test_rate_3_of_5():
    v, lo, hi = rate_with_range(3, 5)
    assert v == round(4 / 7, 3)
    assert lo < v < hi


def test_range_contains_value_and_widens_with_fewer_trials():
    for s, n in [(0, 4), (4, 4), (2, 10), (30, 60)]:
        v, lo, hi = rate_with_range(s, n)
        assert lo <= v <= hi
    _, lo1, hi1 = rate_with_range(1, 2)
    _, lo2, hi2 = rate_with_range(50, 100)
    assert hi1 - lo1 > hi2 - lo2


def test_p_better_symmetry_and_direction():
    ab = p_better(8, 10, 2, 10)
    ba = p_better(2, 10, 8, 10)
    assert abs(ab + ba - 1) < 0.02
    assert ab > 0.95
    assert abs(p_better(5, 10, 5, 10) - 0.5) < 0.03


def _ev(i, ts, type_, payload, actor="user"):
    return {"id": f"ev_{i:06d}", "ts": ts, "type": type_, "actor": actor, "subject": "x", "payload": payload}


def test_censoring_excludes_young_unreplied_send():
    evs = [_ev(1, "2026-11-09T08:00:00Z", "draft_approved", {"channel": "A", "lead": "l1", "template": "v1"}),
           _ev(2, "2026-11-01T08:00:00Z", "draft_approved", {"channel": "A", "lead": "l0", "template": "v1"})]
    m = compute_metrics(evs, "2026-W46", now="2026-11-10T08:00:00Z")
    a = m["channels"][0]
    assert a["sent"] == 1 and a["replies"] == 0  # only the old one counts


def test_young_send_with_reply_counts():
    evs = [_ev(1, "2026-11-09T08:00:00Z", "draft_approved", {"channel": "A", "lead": "l1", "template": "v1"}),
           _ev(2, "2026-11-10T00:00:00Z", "reply_received", {"channel": "A", "lead": "l1"}, "world")]
    m = compute_metrics(evs, "2026-W46", now="2026-11-10T08:00:00Z")
    assert m["channels"][0]["sent"] == 1 and m["channels"][0]["replies"] == 1


def test_guardrails():
    evs = [_ev(1, "2026-11-09T08:00:00Z", "draft_sent", {"lead": "z"}),
           _ev(2, "2026-11-09T08:01:00Z", "draft_approved", {"channel": "A", "lead": "l1"}),
           _ev(3, "2026-11-09T08:02:00Z", "draft_approved", {"channel": "B", "lead": "l1"}),
           _ev(4, "2026-11-09T08:03:00Z", "rate_quoted", {"rate": 50, "floor": 80})]
    g = compute_metrics(evs, "2026-W46")["guardrails"]
    assert g == {"unapproved_sends": 1, "below_floor_quotes": 1, "unsourced_claims": 0, "duplicate_contacts": 1}


def test_shape_matches_fixture():
    m = compute_metrics(simulate(14), "2026-W46")
    assert set(m) == set(FIXTURE) - {"_sample"}
    assert [h["name"] for h in m["headline"]] == [h["name"] for h in FIXTURE["headline"]]
    assert set(m["headline"][0]) == set(FIXTURE["headline"][0])
    assert set(m["channels"][0]) == set(FIXTURE["channels"][0])
    assert set(m["guardrails"]) == set(FIXTURE["guardrails"])
    json.dumps(m)


def test_empty_events():
    m = compute_metrics([], "2026-W46")
    assert m["channels"] == [] and m["headline"][0]["display"] == "—"


def test_simulate_deterministic_and_ordered():
    a, b = simulate(10, seed=3), simulate(10, seed=3)
    assert a == b
    assert a[0]["id"] == "ev_000001"
    assert [e["ts"] for e in a] == sorted(e["ts"] for e in a)


def _rank(m, better, worse):
    texts = [p["text"] for p in m["learning"]]
    return f"{better} replies more than {worse}" in texts, f"{worse} replies more than {better}" in texts


def test_learner_finds_real_pattern():
    rates = {"Direct email": 0.8, "RemoteOK": 0.1}
    found = 0
    for seed in range(5):
        evs = simulate(30, seed=seed, true_rates=rates)
        m = compute_metrics(evs, "2026-W47")
        good, bad = _rank(m, "Direct email", "RemoteOK")
        assert not bad
        found += good
    assert found >= 4


def test_no_false_lesson_from_noise():
    rates = {c: 0.35 for c in ["Direct email", "HN Who's Hiring", "Web search", "RemoteOK"]}
    hits = 0
    for seed in range(20):
        m = compute_metrics(simulate(14, seed=seed, true_rates=rates), "2026-W46")
        hits += bool(m["learning"])
    rate = hits / 20
    print(f"false-lesson rate: {rate:.2f}")
    assert rate <= 0.20
