"""Real chat: OpenAI Responses API, streamed, with read-only tools over the user's data.

main.py calls `stream_chat(messages, store, settings)` and turns each yielded
event into a server-sent event. Events follow docs/contract.md:
    {"type": "text", "delta": "..."}
    {"type": "tool", "name": "...", "status": "running" | "done"}
    {"type": "error", "message": "..."}
    {"type": "done"}

Chat can only READ (profile, rate, offers, leads, drafts, metrics). It can't send,
approve or change anything; it points the user to the screen that does.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from ..config import Settings
from . import client as ai

MAX_TOOL_ROUNDS = 4
MAX_HISTORY = 20  # messages sent to the model

INSTRUCTIONS = """You are Chipi, a business manager for one freelancer. You help them decide what to sell, what to charge, which leads to chase and how to follow up.

How you answer:
- Use the tools to look at the user's real data before answering anything about their profile, rate, offers, leads, drafts or results. Never guess these.
- Only state facts that a tool returned or the user told you. If the data isn't there yet, say what's missing and which screen fills it (Your profile, Leads, Drafts).
- When you give a number (a rate, a score, a reply rate), say where it comes from.
- You can't send, approve or change anything. To act, point to the screen: Drafts to approve or reject, Leads to mark useful or skip.
- Keep answers short and plain: a few sentences or a short list. No headings."""


def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool("get_profile", "The user's persona (skills, experience, what they want and avoid) and settings."),
    _tool("get_fit_and_rate", "Where the user fits best, and the suggested rate (floor, ask, range) with its reasons and sources."),
    _tool("get_offers", "The packaged offers suggested for the user."),
    _tool(
        "list_leads",
        "Job leads with match score and why. Optionally filter by status.",
        {"status": {"type": ["string", "null"], "enum": ["new", "useful", "maybe", "skip", "drafted", "sent", "replied", "won", "lost", None],
                    "description": "Only leads with this status; null for all."}},
        ["status"],
    ),
    _tool(
        "list_drafts",
        "Outreach drafts and their verifier checks. Optionally filter by status.",
        {"status": {"type": ["string", "null"], "enum": ["pending", "approved", "sent", "rejected", None],
                    "description": "Only drafts with this status; null for all."}},
        ["status"],
    ),
    _tool("get_pipeline", "Leads grouped by stage, plus upcoming deadlines."),
    _tool("get_metrics", "Reply rates by channel (with ranges), guardrail counters and proposed lessons."),
]


def _trim_leads(data: dict) -> dict:
    keep = ("nickname", "title", "company", "source", "rate_text", "score", "why", "status", "url")
    return {"leads": [{k: lead.get(k) for k in keep} for lead in data.get("leads", [])[:25]]}


def _trim_drafts(data: dict) -> dict:
    out = []
    for d in data.get("drafts", [])[:10]:
        out.append({k: d.get(k) for k in ("nickname", "channel", "subject", "body", "status")} |
                   {"checks": d.get("verifier", {}).get("checks", [])})
    return {"drafts": out}


def _handlers(store, settings: Settings) -> dict[str, Callable[[dict], Any]]:
    def metrics(_: dict) -> dict:
        from ..metrics import compute_metrics
        from ..storage.events import read_events

        now = datetime.now(timezone.utc)
        year, week, _w = now.isocalendar()
        return compute_metrics(read_events(settings.data_dir), f"{year}-W{week:02d}")

    return {
        "get_profile": lambda _a: store.get("persona"),
        "get_fit_and_rate": lambda _a: store.get("fit"),
        "get_offers": lambda _a: store.get("offers"),
        "list_leads": lambda a: _trim_leads(store.leads(a.get("status"))),
        "list_drafts": lambda a: _trim_drafts(store.drafts(a.get("status"))),
        "get_pipeline": lambda _a: store.get("pipeline"),
        "get_metrics": metrics,
    }


def run_tool(name: str, arguments: str, store, settings: Settings) -> str:
    """Run one tool call; always returns a JSON string (errors included) for the model."""
    handler = _handlers(store, settings).get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool {name}"})
    try:
        args = json.loads(arguments or "{}")
        return json.dumps(handler(args), default=str)[:20000]
    except Exception as exc:  # report to the model rather than crash the stream
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]})


async def stream_chat(messages: list[dict], store, settings: Settings) -> AsyncIterator[dict]:
    import openai

    try:
        model = ai.model_for("smart", settings)
    except ai.AIUnavailable as exc:
        yield {"type": "error", "message": f"Chat can't reach the model: {exc}"}
        yield {"type": "done"}
        return

    client = ai.get_async_client()
    history = [{"role": m["role"], "content": m["content"]} for m in messages[-MAX_HISTORY:]]
    request: dict[str, Any] = {"model": model, "instructions": INSTRUCTIONS, "input": history, "tools": TOOLS}

    try:
        for _round in range(MAX_TOOL_ROUNDS + 1):
            async with client.responses.stream(**request) as stream:
                async for event in stream:
                    if getattr(event, "type", None) == "response.output_text.delta" and event.delta:
                        yield {"type": "text", "delta": event.delta}
                final = await stream.get_final_response()

            calls = [item for item in final.output if getattr(item, "type", None) == "function_call"]
            if not calls:
                break
            if _round == MAX_TOOL_ROUNDS:
                yield {"type": "text", "delta": "\n\n(I looked up as much as I can for one answer. Ask a narrower question?)"}
                break

            outputs = []
            for call in calls:
                yield {"type": "tool", "name": call.name, "status": "running"}
                outputs.append({"type": "function_call_output", "call_id": call.call_id,
                                "output": run_tool(call.name, call.arguments, store, settings)})
                yield {"type": "tool", "name": call.name, "status": "done"}
            # Continue the same response chain with the tool results
            request = {"model": model, "instructions": INSTRUCTIONS, "tools": TOOLS,
                       "previous_response_id": final.id, "input": outputs}
    except openai.APIError as exc:
        yield {"type": "error", "message": f"The model couldn't answer: {type(exc).__name__}: {exc}"[:400]}

    yield {"type": "done"}
