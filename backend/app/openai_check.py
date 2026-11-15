"""Startup check: which OpenAI models can this key use, and are the configured ones among them?

Runs once at startup when OPENAI_API_KEY is set. Logs only model IDs, never the key.
Never raises: a failed check is logged and the app keeps starting.
"""

import asyncio
import logging

from .config import Settings

log = logging.getLogger("chipi.openai")

# Filled by check_models(); /health reports it.
STATUS: dict = {"checked": False, "available": [], "configured_ok": {}, "error": None}


def _list_model_ids() -> list[str]:
    from openai import OpenAI  # imported lazily so tests without the key never touch it

    client = OpenAI(timeout=10, max_retries=1)
    return sorted(m.id for m in client.models.list())


async def check_models(settings: Settings) -> None:
    if not settings.openai_key_set:
        log.warning("OPENAI_API_KEY is not set; model calls will fail until it is.")
        return
    try:
        ids = await asyncio.to_thread(_list_model_ids)
    except Exception as exc:  # network, auth, anything: report and carry on
        STATUS.update(checked=True, error=f"{type(exc).__name__}: {exc}"[:300])
        log.error("Could not list OpenAI models: %s", STATUS["error"])
        return

    text_models = [i for i in ids if i.startswith(("gpt-", "o"))]
    configured = {"smart": settings.model_smart, "check": settings.model_check, "fast": settings.model_fast}
    STATUS.update(
        checked=True,
        available=text_models,
        configured_ok={role: (name in ids) for role, name in configured.items() if name},
        error=None,
    )
    log.info("OpenAI models available to this key: %s", ", ".join(text_models) or "(none)")
    for role, name in configured.items():
        if not name:
            log.warning("OPENAI_MODEL_%s is not set", role.upper())
        elif name not in ids:
            log.error("OPENAI_MODEL_%s=%s is not available to this key", role.upper(), name)
