"""Real chat (app/ai/chat.py) with a fake OpenAI streaming client: no network."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai import chat as chat_mod
from app.config import REPO_ROOT, Settings
from app.main import create_app


class FakeStream:
    def __init__(self, deltas, output, response_id):
        self.deltas, self.output, self.id = deltas, output, response_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for d in self.deltas:
                yield SimpleNamespace(type="response.output_text.delta", delta=d)
        return gen()

    async def get_final_response(self):
        return SimpleNamespace(id=self.id, output=self.output)


class FakeClient:
    """Each call to responses.stream pops the next scripted turn and records the request."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.responses = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        self.requests.append(request)
        return self.turns.pop(0)


def call(name, args, call_id="c1"):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


def settings(tmp_path, **over):
    base = dict(mock_mode=False, data_dir=tmp_path / "data", fixtures_dir=REPO_ROOT / "fixtures", mcp_token="t",
                cors_origins=(), mock_stream_delay=0, web_dist=tmp_path / "x", landing_file=tmp_path / "y.html",
                openai_key_set=True, model_smart="gpt-test", model_check="gpt-test", model_fast="gpt-test")
    return Settings(**{**base, **over})


def events(client, text="What should I charge?"):
    res = client.post("/api/chat", json={"messages": [{"role": "user", "content": text}]})
    assert res.status_code == 200
    return [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]


@pytest.fixture()
def app_with(tmp_path, monkeypatch):
    def make(fake, **over):
        monkeypatch.setattr(chat_mod.ai, "get_async_client", lambda: fake)
        return TestClient(create_app(settings(tmp_path, **over)))
    return make


def test_streams_text_and_ends_with_done(app_with):
    fake = FakeClient([FakeStream(["Hi ", "Sam."], [], "r1")])
    with app_with(fake) as c:
        ev = events(c)
    assert [e["delta"] for e in ev if e["type"] == "text"] == ["Hi ", "Sam."]
    assert ev[-1] == {"type": "done"}
    req = fake.requests[0]
    assert req["model"] == "gpt-test" and req["input"][0]["content"] == "What should I charge?"
    assert all(t["strict"] for t in req["tools"])


def test_runs_a_tool_then_answers_with_its_result(app_with):
    fake = FakeClient([
        FakeStream([], [call("get_fit_and_rate", {})], "r1"),
        FakeStream(["Your floor is $100."], [], "r2"),
    ])
    with app_with(fake) as c:
        ev = events(c)
    assert {"type": "tool", "name": "get_fit_and_rate", "status": "running"} in ev
    assert {"type": "tool", "name": "get_fit_and_rate", "status": "done"} in ev
    second = fake.requests[1]
    assert second["previous_response_id"] == "r1"
    output = json.loads(second["input"][0]["output"])
    assert output["rate"]["floor"] == 100  # real data from goal.md, not invented


def test_list_leads_filters_and_trims(app_with):
    fake = FakeClient([FakeStream([], [call("list_leads", {"status": None})], "r1"), FakeStream(["ok"], [], "r2")])
    with app_with(fake) as c:
        c.post("/api/seeds", json={"url": "https://example.com/jobs/1"})
        events(c, "Any leads?")
    leads = json.loads(fake.requests[1]["input"][0]["output"])["leads"]
    assert len(leads) == 1 and set(leads[0]) <= {"nickname", "title", "company", "source", "rate_text", "score", "why", "status", "url"}


def test_unknown_tool_and_bad_args_are_reported_to_the_model(tmp_path):
    s = settings(tmp_path)
    assert "Unknown tool" in chat_mod.run_tool("send_email", "{}", None, s)
    assert "error" in json.loads(chat_mod.run_tool("list_leads", "not json", None, s))


def test_stops_after_too_many_tool_rounds(app_with):
    turns = [FakeStream([], [call("get_profile", {}, f"c{i}")], f"r{i}") for i in range(chat_mod.MAX_TOOL_ROUNDS + 1)]
    with app_with(FakeClient(turns)) as c:
        ev = events(c)
    assert any("narrower question" in e.get("delta", "") for e in ev)
    assert ev[-1] == {"type": "done"}


def test_no_model_configured_gives_a_clear_error(app_with):
    with app_with(FakeClient([]), model_smart="") as c:
        ev = events(c)
    assert ev[0]["type"] == "error" and "OPENAI_MODEL_SMART" in ev[0]["message"]
    assert ev[-1] == {"type": "done"}


def test_api_error_is_reported_not_raised(app_with):
    import httpx
    import openai

    class Boom(FakeClient):
        def _stream(self, **request):
            raise openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))

    with app_with(Boom([])) as c:
        ev = events(c)
    assert ev[0]["type"] == "error" and ev[-1] == {"type": "done"}
