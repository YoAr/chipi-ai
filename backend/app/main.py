"""Chipi API.

Run locally:  cd backend && uv run uvicorn app.main:app --reload --port 8010
Only mock mode exists so far: every route serves fixtures/ (see docs/contract.md).
"""

import asyncio
import hmac
import json
from typing import AsyncIterator, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import REPO_ROOT, Settings, load_settings
from .mock_store import MockStore, NotFound


# Request bodies (shapes from docs/contract.md) ------------------------------
class VerdictIn(BaseModel):
    verdict: Literal["useful", "maybe", "skip"]
    note: str | None = None


class SeedIn(BaseModel):
    url: str = Field(min_length=8)


class ApproveIn(BaseModel):
    final_text: str = Field(min_length=1)


class RejectIn(BaseModel):
    note: str = ""


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    if not settings.mock_mode:
        raise RuntimeError("Only mock mode is built so far. Set MOCK_MODE=1.")

    store = MockStore(settings.fixtures_dir)
    app = FastAPI(title="Chipi API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Every error uses the contract's shape: {"error": {"code", "message"}}
    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return _error(404, "not_found", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return _error(422, "invalid_request", f"{where}: {first.get('msg', 'invalid')}".strip(": "))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(exc.status_code, "error")
        return _error(exc.status_code, code, str(exc.detail))

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "mode": "mock"}

    # Read routes -----------------------------------------------------------
    @app.get("/api/state")
    def state() -> dict:
        return store.get("state")

    @app.get("/api/persona")
    def persona() -> dict:
        return store.get("persona")

    @app.put("/api/persona")
    def update_persona(fields: dict) -> dict:
        return store.update_persona(fields)

    @app.get("/api/fit")
    def fit() -> dict:
        return store.get("fit")

    @app.get("/api/offers")
    def offers() -> dict:
        return store.get("offers")

    @app.get("/api/leads")
    def leads(status: str | None = None) -> dict:
        return store.leads(status)

    @app.get("/api/drafts")
    def drafts(status: str | None = None) -> dict:
        return store.drafts(status)

    @app.get("/api/pipeline")
    def pipeline() -> dict:
        return store.get("pipeline")

    @app.get("/api/metrics")
    def metrics() -> dict:
        return store.get("metrics")

    # Write routes ----------------------------------------------------------
    @app.post("/api/leads/{lead_id}/verdict")
    def verdict(lead_id: str, body: VerdictIn) -> dict:
        return store.set_verdict(lead_id, body.verdict, body.note)

    @app.post("/api/cv")
    async def upload_cv(file: UploadFile = File(...)) -> dict:
        if not file.filename:
            raise StarletteHTTPException(400, "Choose a file to upload")
        return store.add_cv(file.filename)

    @app.post("/api/seeds", status_code=201)
    def seed(body: SeedIn) -> dict:
        return store.add_seed(body.url)

    @app.post("/api/drafts/{draft_id}/approve")
    def approve(draft_id: str, body: ApproveIn) -> dict:
        return store.approve_draft(draft_id, body.final_text)

    @app.post("/api/drafts/{draft_id}/reject")
    def reject(draft_id: str, body: RejectIn) -> dict:
        return store.reject_draft(draft_id, body.note)

    @app.post("/api/reset")
    def reset(authorization: str = Header(default="")) -> dict:
        expected = f"Bearer {settings.mcp_token}"
        if not settings.mcp_token or not hmac.compare_digest(authorization, expected):
            raise StarletteHTTPException(401, "A valid MCP_TOKEN bearer token is required")
        store.reset()
        return {"ok": True}

    # Chat (mock stream with the contract's event shapes) -------------------
    @app.post("/api/chat")
    async def chat(body: ChatIn) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            delay = settings.mock_stream_delay
            yield _sse({"type": "tool", "name": "suggest_rate", "status": "running"})
            await asyncio.sleep(delay * 5)
            yield _sse({"type": "tool", "name": "suggest_rate", "status": "done"})
            reply = (
                "This is mock mode, so here's a sample answer. Based on your persona and "
                "Boston benchmarks, I'd ask $120 an hour: it sits above break-even and "
                "inside what agencies bill for mid-level engineers."
            )
            for word in reply.split(" "):
                yield _sse({"type": "text", "delta": word + " "})
                await asyncio.sleep(delay)
            yield _sse({"type": "done"})

        return StreamingResponse(events(), media_type="text/event-stream")

    # Website: landing page at "/", React app at "/app/" (registered last, after the API)
    if settings.landing_file.exists():
        @app.get("/", include_in_schema=False)
        def landing() -> FileResponse:
            return FileResponse(settings.landing_file, media_type="text/html")

    if (settings.web_dist / "index.html").exists():
        @app.get("/app", include_in_schema=False)
        def app_redirect() -> RedirectResponse:
            return RedirectResponse("/app/")

        app.mount("/app", StaticFiles(directory=settings.web_dist, html=True), name="web")

    return app


load_dotenv(REPO_ROOT / ".env")
app = create_app()
