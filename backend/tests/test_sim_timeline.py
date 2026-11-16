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


def test_single_flight_under_concurrent_first_calls(monkeypatch):
    import threading
    import time

    from app import sim_timeline

    calls = []

    def slow_compute():
        calls.append(1)
        time.sleep(0.2)
        return {"timeline": [], "simulated": True}

    monkeypatch.setattr(sim_timeline, "_result", None)
    monkeypatch.setattr(sim_timeline, "_compute", slow_compute)
    threads = [threading.Thread(target=sim_timeline.demo_timeline) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1  # eight concurrent first callers, one computation


def test_route_answers_503_while_warming_and_never_computes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import sim_timeline
    from app.config import REPO_ROOT, Settings
    from app.main import create_app

    computed = []
    monkeypatch.setattr(sim_timeline, "_result", None)
    monkeypatch.setattr(sim_timeline, "_compute", lambda: computed.append(1) or {"timeline": [], "simulated": True})
    s = Settings(mock_mode=True, data_dir=tmp_path, fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                 cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html")
    client = TestClient(create_app(s))  # no `with`: the startup warm-up does not run
    res = client.get("/api/metrics", params={"simulate_weeks": "true"})
    assert res.status_code == 503 and res.json()["error"]["code"] == "unavailable"
    assert computed == []  # the request did not start the computation
    sim_timeline.demo_timeline()  # warm-up finishes
    assert client.get("/api/metrics", params={"simulate_weeks": "true"}).json()["simulated"] is True
