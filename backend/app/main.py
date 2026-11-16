"""Chipi API.

Run locally:  cd backend && uv run uvicorn app.main:app --reload --port 8010
Only mock mode exists so far: every route serves fixtures/ (see docs/contract.md).
"""

import asyncio
import json
from typing import AsyncIterator, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import REPO_ROOT, Settings, load_settings
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from .ai.client import AIUnavailable
from .cv import extract_text
from .routes import drafts as draft_routes
from .routes import profile as profile_routes
from .routes import sample_cv as sample_cv_routes
from .routes import search as search_routes
from .guards import GuardError, RateLimiter, client_ip, require_token
from .mcp_server import create_mcp, mount_mcp
from .passcode import install as install_passcode
from .metrics import compute_metrics
from .mock_store import MockStore, NotFound
from .openai_check import STATUS as OPENAI_STATUS
from .rates import refresh_benchmarks
from . import jobs, scout
from .providers.search import ProviderUnavailable
from .routes import scout as scout_routes
from .routes import interview as interview_routes
from .routes import services as services_routes
import threading
from .openai_check import check_models
from .simulate import simulate
from .storage import RealStore
from .storage import queue as work_queue
from .storage.events import read_events
from .storage.wiki import save_raw

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# "Simulate 2 weeks" demo: channel reply rates chosen so one lesson clears the
# Learner's confidence bar (see tests/test_metrics.py for the no-false-lesson check).
DEMO_TRUE_RATES = {"Direct email": 0.65, "HN Who's Hiring": 0.45, "Web search": 0.3, "RemoteOK": 0.1}
DEMO_SIM = {"days": 14, "seed": 3, "sends_per_day": 6}


def _iso_week(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


class _QueueAdapter:
    """What the MCP tools need: pending() and complete(). Mock mode has no queue."""

    def __init__(self, store, settings: Settings | None = None):
        self.store = store
        self.settings = settings

    def pending(self) -> list[dict]:
        return work_queue.pending(self.store.conn) if isinstance(self.store, RealStore) else []

    def complete(self, id: int, ok: bool, error: str | None = None) -> dict:
        if not isinstance(self.store, RealStore):
            raise NotFound("Mock mode has no work queue")
        return work_queue.complete(self.store.conn, id, ok, error)

    def refresh_rates(self) -> dict:
        if not isinstance(self.store, RealStore):
            raise NotFound("Mock mode serves fixed sample rates")
        return refresh_benchmarks(self.store.data_dir)

    def start_job(self, key: str, fn, *args) -> dict:
        """Start a background job (one run per key) and return its status for the routine to poll."""
        if not isinstance(self.store, RealStore):
            raise NotFound("Not available with sample data (MOCK_MODE=1)")
        if jobs.begin(self.store, key, step="starting"):
            threading.Thread(target=jobs.run, args=(self.store, key, fn, self.store, self.settings, *args),
                             daemon=True, name=f"job-{key}").start()
        return jobs.get(self.store, key)

    def start_scout(self, terms: list[str] | None) -> dict:
        return self.start_job(scout.JOB, scout.run_scout, terms)

    def start_snapshot(self) -> dict:
        return self.start_job(scout.SNAPSHOT_JOB, scout.run_snapshot)

    def job_status(self, key: str) -> dict:
        if key not in (scout.JOB, scout.SNAPSHOT_JOB):
            raise NotFound(f"Unknown job {key}")
        return jobs.get(self.store, key) if isinstance(self.store, RealStore) else jobs._idle()


# Request bodies (shapes from docs/contract.md) ------------------------------
class VerdictIn(BaseModel):
    verdict: Literal["useful", "maybe", "skip"]
    note: str | None = None


class SeedIn(BaseModel):
    # http(s) only: the URL is rendered as a link, so javascript: and data: are refused
    url: HttpUrl


class PersonaIn(BaseModel):
    """Fields the user may edit; anything else is rejected rather than stored."""

    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    years: int | None = Field(default=None, ge=0, le=60)
    location: str | None = Field(default=None, max_length=120)
    skills: list[str] | None = Field(default=None, max_length=40)
    wants: str | None = Field(default=None, max_length=500)
    avoids: str | None = Field(default=None, max_length=500)
    hours_per_week: int | None = Field(default=None, ge=1, le=80)
    income_goal_monthly: int | None = Field(default=None, ge=0, le=1_000_000)


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
    if settings.mock_mode:
        store = MockStore(settings.fixtures_dir)
    else:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        store = RealStore(settings.data_dir, REPO_ROOT / "data")  # seeds data/ on first start
    token_required = require_token(settings.mcp_token)
    chat_limiter = RateLimiter(max_calls=20, per_seconds=60)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await check_models(settings)
        yield

    app = FastAPI(title="Chipi API", version="0.3.0", lifespan=lifespan)
    # Shared with the route modules in app/routes/ (see app/deps.py)
    app.state.store = store
    app.state.settings = settings
    app.state.action_limiter = RateLimiter(max_calls=10, per_seconds=60)
    app.include_router(profile_routes.router)
    app.include_router(search_routes.router)
    app.include_router(draft_routes.router)
    app.include_router(sample_cv_routes.router)
    app.include_router(scout_routes.router)
    app.include_router(interview_routes.router)
    app.include_router(services_routes.router)
    install_passcode(app, settings)  # no-op unless DEMO_PASSCODE is set (public deploy)
    app.add_middleware(  # added last so it runs first: CORS headers on every reply, 401s included
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
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 413: "too_large",
                415: "unsupported_file", 429: "rate_limited"}.get(exc.status_code, "error")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(ProviderUnavailable)
    async def _provider_down(_: Request, exc: ProviderUnavailable) -> JSONResponse:
        return _error(503, "provider_unavailable", str(exc))

    @app.exception_handler(AIUnavailable)
    async def _ai_down(_: Request, exc: AIUnavailable) -> JSONResponse:
        return _error(503, "model_unavailable", str(exc))

    @app.exception_handler(GuardError)
    async def _blocked(_: Request, exc: GuardError) -> JSONResponse:
        return _error(422, "blocked", str(exc))

    @app.exception_handler(ValueError)
    async def _conflict(_: Request, exc: ValueError) -> JSONResponse:
        # e.g. approving a draft that is no longer pending
        return _error(409, "conflict", str(exc))

    @app.get("/health")
    def health() -> dict:
        # Reports whether secrets are present, never their values.
        return {
            "ok": True,
            "mode": "mock" if settings.mock_mode else "real",
            "openai_key_set": settings.openai_key_set,
            "mcp_token_set": bool(settings.mcp_token),
            "models": {
                "smart": settings.model_smart or None,
                "check": settings.model_check or None,
                "fast": settings.model_fast or None,
                "configured_ok": OPENAI_STATUS["configured_ok"],
                "available": OPENAI_STATUS["available"],
                "error": OPENAI_STATUS["error"],
            },
        }

    # Read routes -----------------------------------------------------------
    @app.get("/api/state")
    def state() -> dict:
        return store.get("state")

    @app.get("/api/persona")
    def persona() -> dict:
        return store.get("persona")

    @app.put("/api/persona")
    def update_persona(body: PersonaIn) -> dict:
        return store.update_persona(body.model_dump(exclude_unset=True))

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
    def metrics(simulate_weeks: bool = False) -> dict:
        # ?simulate_weeks=true: the "simulate 2 weeks" demo. Computed from a generated
        # history in memory; nothing is written to the real events log.
        if simulate_weeks:
            events = simulate(true_rates=DEMO_TRUE_RATES, **DEMO_SIM)
            last = datetime.fromisoformat(events[-1]["ts"].replace("Z", "+00:00"))
            return {**compute_metrics(events, _iso_week(last)), "simulated": True}
        if settings.mock_mode:
            return store.get("metrics")
        return compute_metrics(read_events(settings.data_dir), _iso_week(datetime.now(timezone.utc)))

    # Write routes ----------------------------------------------------------
    @app.post("/api/leads/{lead_id}/verdict")
    def verdict(lead_id: str, body: VerdictIn) -> dict:
        return store.set_verdict(lead_id, body.verdict, body.note)

    @app.post("/api/cv")
    async def upload_cv(file: UploadFile = File(...)) -> dict:
        if not file.filename:
            raise StarletteHTTPException(400, "Choose a file to upload")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise StarletteHTTPException(413, "That file is over 10 MB")
        try:
            text = extract_text(file.filename, content)
        except ValueError as exc:
            raise StarletteHTTPException(415, str(exc)) from exc
        if settings.mock_mode:
            return store.add_cv(file.filename)
        persona = store.add_cv(file.filename, content)
        text_path = save_raw(settings.data_dir, f"{file.filename}.txt", text.encode())  # text for the model
        store.set_setting("cv_text_path", text_path)  # the profile pipeline reads the latest CV from here
        return persona

    @app.post("/api/seeds", status_code=201)
    def seed(body: SeedIn) -> dict:
        return store.add_seed(str(body.url))

    @app.post("/api/drafts/{draft_id}/approve")
    def approve(draft_id: str, body: ApproveIn) -> dict:
        return store.approve_draft(draft_id, body.final_text)

    @app.post("/api/drafts/{draft_id}/reject")
    def reject(draft_id: str, body: RejectIn) -> dict:
        return store.reject_draft(draft_id, body.note)

    @app.post("/api/reset", dependencies=[Depends(token_required)])
    def reset() -> dict:
        store.reset()
        return {"ok": True}

    # Chat (mock stream with the contract's event shapes) -------------------
    @app.post("/api/chat")
    async def chat(body: ChatIn, request: Request) -> StreamingResponse:
        if not chat_limiter.check(client_ip(request)):
            raise StarletteHTTPException(429, "Too many messages. Wait a minute and try again.")

        async def not_connected() -> AsyncIterator[str]:
            yield _sse({"type": "error", "message": "Chat isn't connected to the model yet."})
            yield _sse({"type": "done"})

        if not settings.mock_mode:
            try:
                from .ai.chat import stream_chat  # async generator of contract chat events
            except ImportError:
                return StreamingResponse(not_connected(), media_type="text/event-stream")
            messages = [m.model_dump() for m in body.messages]

            async def real_events() -> AsyncIterator[str]:
                async for event in stream_chat(messages, store, settings):
                    yield _sse(event)

            return StreamingResponse(real_events(), media_type="text/event-stream")

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

    # Routine endpoint: same process, token-protected (see app/mcp_server.py)
    mount_mcp(app, create_mcp(lambda: _QueueAdapter(store, settings)), settings.mcp_token)

    # Website: landing page at "/", React app at "/app/" (registered last, after the API)
    if settings.landing_file.exists():
        @app.get("/", include_in_schema=False)
        def landing() -> FileResponse:
            return FileResponse(settings.landing_file, media_type="text/html")

        # Information pages linked from the landing page footer
        for slug, filename in (("privacy", "privacy.html"), ("how-we-use-ai", "how-we-use-ai.html"),
                               ("numbers", "numbers.html"), ("contact", "contact.html")):
            page = settings.landing_file.parent / filename
            if page.exists():
                app.add_api_route(f"/{slug}", lambda page=page: FileResponse(page, media_type="text/html"),
                                  methods=["GET"], include_in_schema=False)

        # Landing page assets (illustrations, shared CSS): frontend/landing/static -> /static
        landing_static = settings.landing_file.parent / "static"
        if landing_static.is_dir():
            app.mount("/static", StaticFiles(directory=landing_static), name="landing-static")

    if (settings.web_dist / "index.html").exists():
        @app.get("/app", include_in_schema=False)
        def app_redirect() -> RedirectResponse:
            return RedirectResponse("/app/")

        app.mount("/app", StaticFiles(directory=settings.web_dist, html=True), name="web")

    return app


load_dotenv(REPO_ROOT / ".env")
app = create_app()
