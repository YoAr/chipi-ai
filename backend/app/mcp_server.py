"""MCP endpoint (/mcp) for the laptop routine.

Tools are thin: they call a store object supplied by `store_provider`, so the
queue module (app/storage/queue.py) is never imported here and tests use fakes.
The store must offer `.pending() -> list[dict]` and `.complete(id, ok, error)`.

Wiring in main.py (inside create_app, after the routes):

    from .mcp_server import create_mcp, mount_mcp
    mount_mcp(app, create_mcp(store_provider), settings.mcp_token)

`mount_mcp` also hooks the MCP session manager into the app's lifespan, so no
other wiring is needed. Mounted sub-apps do NOT get their own lifespan run by
Starlette, which is why this is done on the parent app.
"""

from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Mount, Route

StoreProvider = Callable[[], Any]


def create_mcp(store_provider: StoreProvider) -> MCPServer:
    """Build the MCP server. `store_provider()` is called per tool call."""
    mcp = MCPServer("chipi")

    @mcp.tool()
    def ping() -> str:
        """Health check: returns "pong" and the server time (UTC)."""
        return f"pong {datetime.now(timezone.utc).isoformat()}"

    @mcp.tool()
    def get_queue() -> list[dict]:
        """Pending queue items the routine should work on, oldest first."""
        return list(store_provider().pending())

    @mcp.tool()
    def complete_queue_item(id: int, ok: bool, error: str | None = None) -> dict:
        """Mark a queue item done (ok=true) or failed (ok=false, with an error message)."""
        store_provider().complete(id, ok, error)
        return {"ok": True, "id": id}

    @mcp.tool()
    def refresh_rate_benchmarks() -> dict:
        """Refresh Dutch freelance rate benchmarks (EUR/hour) from ZZPdock market data.

        Saves each extract in raw/ and rewrites benchmarks.json. Returns which vakgebieden
        were updated and which failed (the old figures are kept for those). Run daily.
        """
        return store_provider().refresh_rates()

    @mcp.tool()
    def run_scout(terms: list[str] | None = None) -> dict:
        """Start a Scout run: find Amsterdam/NL Recruitee boards for the discovery terms (default:
        the approved services' terms), read their postings, filter (≤ 30 days, NL or remote,
        exclusions), tag language and skills, and add new leads. Returns the job; poll get_job("scout")."""
        return store_provider().start_scout(terms)

    @mcp.tool()
    def snapshot_panel() -> dict:
        """Start today's baseline-panel snapshot (demand index input). Run daily.
        Returns the job; poll get_job("panel_snapshot")."""
        return store_provider().start_snapshot()

    @mcp.tool()
    def research_leads(limit: int = 10) -> dict:
        """Start the Researcher on the newest Scout leads (status "filtered"): for each, find a recent
        dated company hook, pick the matching approved service and channel, or drop it with a reason.
        Returns the job; poll get_job("research")."""
        return store_provider().start_research(max(1, min(int(limit), 25)))

    @mcp.tool()
    def write_drafts(limit: int = 10) -> dict:
        """Start the Writer + Verifier on researched leads without a draft. Drafts land as "ready" or
        "needs_human"; nothing is sent (the freelancer copies them). Poll get_job("drafts")."""
        return store_provider().start_drafts(max(1, min(int(limit), 25)))

    @mcp.tool()
    def get_job(key: str) -> dict:
        """Status of a background job started from here: "scout", "panel_snapshot", "research" or "drafts"."""
        return store_provider().job_status(key)

    return mcp


class TokenMiddleware:
    """Pure ASGI: 401 JSON unless `Authorization: Bearer <token>` matches."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        got = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {self.token}"
        if not self.token or not hmac.compare_digest(got.encode(), expected.encode()):
            body = json.dumps(
                {"error": {"code": "unauthorized", "message": "A valid MCP_TOKEN bearer token is required"}}
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def mount_mcp(app: FastAPI, mcp_server: MCPServer, token: str) -> None:
    """Serve the MCP streamable-HTTP endpoint at /mcp behind the bearer token."""
    # Host/Origin allow-listing is off: the app is reached as chipi-ai.fly.dev and the
    # bearer token is the guard. (The SDK default only allows localhost Host headers.)
    mcp_server.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )  # builds mcp_server.session_manager; its Starlette wrapper is not used
    guarded = TokenMiddleware(StreamableHTTPASGIApp(mcp_server.session_manager), token)

    # Both /mcp and /mcp/... (a bare Mount would 307-redirect /mcp to /mcp/, which breaks POST clients).
    app.router.routes.insert(0, Route("/mcp", endpoint=guarded))
    app.router.routes.insert(1, Mount("/mcp", app=guarded))

    # Starlette does not run a mounted app's lifespan, so run the session manager on the parent's.
    parent = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with mcp_server.session_manager.run():
            async with parent(a) as state:
                yield state

    app.router.lifespan_context = lifespan
