import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.mcp_server import create_mcp, mount_mcp

TOKEN = "secret-token"
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class FakeStore:
    def __init__(self):
        self.items = [{"id": 1, "kind": "draft", "ref": "d1"}]
        self.completed = []

    def pending(self):
        return self.items

    def complete(self, id, ok, error=None):
        self.completed.append((id, ok, error))


def make_app(token=TOKEN):
    store = FakeStore()
    app = FastAPI()
    mount_mcp(app, create_mcp(lambda: store), token)
    return app, store


def rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


def parse(res):
    if res.headers["content-type"].startswith("text/event-stream"):
        line = next(l for l in res.text.splitlines() if l.startswith("data:"))
        return json.loads(line[5:])
    return res.json()


INIT = rpc(
    "initialize",
    {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
)


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}])
def test_bad_token_is_401(headers):
    app, _ = make_app()
    with TestClient(app) as c:
        res = c.post("/mcp", json=INIT, headers={**ACCEPT, **headers})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_empty_token_always_401():
    app, _ = make_app(token="")
    with TestClient(app) as c:
        for auth in ("Bearer ", "Bearer", ""):
            res = c.post("/mcp", json=INIT, headers={**ACCEPT, "Authorization": auth})
            assert res.status_code == 401


def test_tools_via_fakes():
    store = FakeStore()
    mcp = create_mcp(lambda: store)
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == {"ping", "get_queue", "complete_queue_item", "refresh_rate_benchmarks", "run_scout", "snapshot_panel", "get_job"}


def test_end_to_end_initialize_list_and_call():
    app, store = make_app()
    auth = {**ACCEPT, "Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        res = c.post("/mcp", json=INIT, headers=auth)
        assert res.status_code == 200, res.text
        assert parse(res)["result"]["serverInfo"]["name"] == "chipi"

        res = c.post("/mcp", json=rpc("tools/list", id=2), headers=auth)
        names = {t["name"] for t in parse(res)["result"]["tools"]}
        assert names == {"ping", "get_queue", "complete_queue_item", "refresh_rate_benchmarks", "run_scout", "snapshot_panel", "get_job"}

        res = c.post("/mcp", json=rpc("tools/call", {"name": "ping", "arguments": {}}, 3), headers=auth)
        assert "pong" in json.dumps(parse(res)["result"])

        res = c.post("/mcp", json=rpc("tools/call", {"name": "get_queue", "arguments": {}}, 4), headers=auth)
        assert "d1" in json.dumps(parse(res)["result"])

        res = c.post(
            "/mcp",
            json=rpc("tools/call", {"name": "complete_queue_item", "arguments": {"id": 1, "ok": False, "error": "boom"}}, 5),
            headers=auth,
        )
        assert not parse(res)["result"].get("isError")
        assert store.completed == [(1, False, "boom")]
