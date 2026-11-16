"""The 'Simulate 2 weeks' timeline: real day-by-day snapshots that end on a supported lesson."""

from app.sim_timeline import DEMO_SIM, demo_timeline


def test_timeline_has_one_snapshot_per_day_starting_empty():
    t = demo_timeline()
    days = [p["day"] for p in t["timeline"]]
    assert days == list(range(DEMO_SIM["days"] + 1))
    first = t["timeline"][0]
    assert first["channels"] == [] and first["learning"] == []


def test_sends_only_grow_and_a_lesson_appears_by_the_end():
    points = demo_timeline()["timeline"]
    total = [sum(c["sent"] for c in p["channels"]) for p in points]
    assert total == sorted(total) and total[-1] > 0
    assert points[-1]["learning"], "the demo should end with one supported lesson"
    first_lesson_day = next(p["day"] for p in points if p["learning"])
    assert first_lesson_day > 0  # learned over time, not on day 0


def test_final_snapshot_matches_the_summary():
    t = demo_timeline()
    assert t["simulated"] is True
    assert [l["text"] for l in t["learning"]] == [l["text"] for l in t["timeline"][-1]["learning"]]
    assert [h["direction"] for h in t["headline"]] == ["up", "down"]


def test_is_cached():
    assert demo_timeline() is demo_timeline()
