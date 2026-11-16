"""Background job status for long AI actions ("202 now, poll status").

A job is a small dict stored in the settings table under "job:<key>", in the
contract's Job shape (docs/contract.md → AI actions). Only one run per key at a
time: `begin` returns False if one is already running (and not stale).

    if not jobs.begin(store, "search", step="fetching"):
        return jobs.get(store, "search")          # already running
    background_tasks.add_task(run_search, ...)   # which calls jobs.step / done / fail
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

STALE_AFTER = timedelta(minutes=10)  # a "running" job older than this is treated as dead


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _idle() -> dict:
    return {"status": "idle", "step": None, "message": None, "error": None, "result": None, "updated_at": None}


def get(store, key: str) -> dict:
    return {**_idle(), **(store.get_setting(f"job:{key}") or {})}


def _put(store, key: str, **fields) -> dict:
    job = {**get(store, key), **fields, "updated_at": _now()}
    store.set_setting(f"job:{key}", job)
    return job


def _is_live(job: dict) -> bool:
    if job["status"] != "running" or not job["updated_at"]:
        return False
    started = datetime.strptime(job["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started < STALE_AFTER


def begin(store, key: str, step: str, message: str | None = None) -> bool:
    """Mark the job running. False if a live run already exists (don't start another)."""
    if _is_live(get(store, key)):
        return False
    _put(store, key, status="running", step=step, message=message, error=None, result=None)
    return True


def step(store, key: str, step: str, message: str | None = None) -> dict:
    return _put(store, key, status="running", step=step, message=message)


def done(store, key: str, message: str | None = None, result: dict | None = None) -> dict:
    return _put(store, key, status="done", step=None, message=message, result=result, error=None)


def fail(store, key: str, error: str) -> dict:
    return _put(store, key, status="error", error=error[:400])


def run(store, key: str, fn, *args) -> None:
    """Background-task wrapper: run fn(*args); any failure becomes a readable job error.

    fn reports its own progress with step()/done(). If it returns without calling
    done(), the job is marked done here.
    """
    from .ai.client import AIUnavailable
    from .mock_store import NotFound

    try:
        fn(*args)
    except NotImplementedError as exc:
        fail(store, key, f"Not built yet: {exc}")
        return
    except (AIUnavailable, ValueError, NotFound) as exc:
        fail(store, key, str(exc))
        return
    except Exception as exc:  # never leave a job stuck at "running"
        fail(store, key, f"Something went wrong: {type(exc).__name__}: {exc}")
        return
    if get(store, key)["status"] == "running":
        done(store, key)
