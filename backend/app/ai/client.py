"""Every model call goes through here.

- `structured(...)` returns a validated Pydantic object (OpenAI Responses API,
  `responses.parse(text_format=...)`). Use it for anything the code reads.
- `get_client()` for the rare call that needs streaming (chat).
- Roles, not model names: "smart" | "check" | "fast" map to OPENAI_MODEL_* settings.

Tests never hit the network: monkeypatch `app.ai.client.structured` (or
`get_client`) in the module under test.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from ..config import Settings

Role = Literal["smart", "check", "fast"]
T = TypeVar("T", bound=BaseModel)


class AIUnavailable(Exception):
    """The model could not be used (no key, no model configured, API error). Routes map it to 503."""


@lru_cache(maxsize=1)
def get_client():
    from openai import OpenAI  # lazy: tests and mock mode never import a live client

    return OpenAI(timeout=90, max_retries=2)


@lru_cache(maxsize=1)
def get_async_client():
    """Async client for streaming (chat)."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(timeout=90, max_retries=2)


def model_for(role: Role, settings: Settings) -> str:
    if not settings.openai_key_set:
        raise AIUnavailable("OPENAI_API_KEY is not set")
    name = {"smart": settings.model_smart, "check": settings.model_check, "fast": settings.model_fast}[role]
    if not name:
        raise AIUnavailable(f"OPENAI_MODEL_{role.upper()} is not set")
    return name


def structured(
    role: Role,
    settings: Settings,
    *,
    instructions: str,
    input: str | list[dict[str, Any]],
    schema: type[T],
    tools: list[dict[str, Any]] | None = None,
    store: bool | None = None,
) -> T:
    """One model call that must come back as `schema`. Raises AIUnavailable on any failure."""
    import openai

    kwargs: dict[str, Any] = {
        "model": model_for(role, settings),
        "instructions": instructions,
        "input": input,
        "text_format": schema,
    }
    if tools:
        kwargs["tools"] = tools
    if store is not None:
        kwargs["store"] = store
    try:
        response = get_client().responses.parse(**kwargs)
    except openai.APIError as exc:
        raise AIUnavailable(f"{type(exc).__name__}: {exc}"[:400]) from exc
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise AIUnavailable("The model returned no structured output")
    return parsed
