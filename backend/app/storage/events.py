"""Append-only event log: data_dir/events.jsonl (shape: docs/events.md).

The file is only ever opened for appending; nothing here rewrites or edits it.
Ids are "ev_" + a zero-padded sequence (ev_0001, ev_0002, …; the number keeps
growing past 9999). Sequence order is numeric, not lexical.
"""

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

try:  # cross-process safety where available (Linux/macOS)
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

EVENTS_FILENAME = "events.jsonl"

USER_TYPES = (
    "lead_verdict", "draft_edited", "draft_approved", "draft_rejected", "draft_sent", "rate_decision",
    "steering_note", "seed_added", "goal_core_edited", "persona_updated",
)
WORLD_TYPES = (
    "reply_received", "no_reply_timeout", "call_booked", "contract_won", "contract_lost",
    "milestone_due", "invoice_paid",
)
AGENT_TYPES = (
    "lead_found", "draft_created", "verifier_failed", "lesson_added", "lesson_applied",
    "run_completed", "run_failed",
)
EVENT_TYPES = frozenset(USER_TYPES + WORLD_TYPES + AGENT_TYPES)
ACTORS = frozenset({"user", "world", "agent"})

_ID_RE = re.compile(r"^ev_(\d+)$")
_registry_lock = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}
# path -> ((inode, size, mtime_ns), last_seq): skips rescanning the file on every append
_seq_cache: dict[str, tuple[tuple[int, int, int], int]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / EVENTS_FILENAME


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _registry_lock:
        return _path_locks.setdefault(key, threading.Lock())


def _seq(event_id: str) -> int:
    m = _ID_RE.match(event_id or "")
    if not m:
        raise ValueError(f"Not an event id: {event_id!r}")
    return int(m.group(1))


def _iter_lines(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line from a crash; skip it, never repair it


def _last_seq(path: Path) -> int:
    key = str(path.resolve())
    try:
        st = path.stat()
    except FileNotFoundError:
        return 0
    stamp = (st.st_ino, st.st_size, st.st_mtime_ns)
    cached = _seq_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    last = 0
    for ev in _iter_lines(path):
        try:
            last = max(last, _seq(str(ev.get("id"))))
        except ValueError:
            continue
    return last


def append_event(data_dir: Path, type: str, actor: str, subject: str, payload: dict | None = None) -> dict:
    """Append one event and return it. Thread-safe; never rewrites the file."""
    if type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type {type!r} (see docs/events.md)")
    if actor not in ACTORS:
        raise ValueError(f"Unknown actor {actor!r}; use user, world or agent")
    if not isinstance(subject, str) or not subject:
        raise ValueError("subject must be a non-empty string")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        with path.open("a+b") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                seq = _last_seq(path) + 1
                event = {
                    "id": f"ev_{seq:04d}",
                    "ts": _now(),
                    "type": type,
                    "actor": actor,
                    "subject": subject,
                    "payload": payload,
                }
                line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                fh.seek(0, os.SEEK_END)
                prefix = b""
                if fh.tell() > 0:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        prefix = b"\n"  # previous write was torn; start on a fresh line
                fh.seek(0, os.SEEK_END)
                fh.write(prefix + line.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
                st = path.stat()
                _seq_cache[str(path.resolve())] = ((st.st_ino, st.st_size, st.st_mtime_ns), seq)
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return event


def read_events(data_dir: Path, since_id: str | None = None) -> list[dict]:
    """All events in file order, or only those after `since_id` (exclusive)."""
    after = _seq(since_id) if since_id else 0
    out = []
    for ev in _iter_lines(_path(data_dir)):
        try:
            if _seq(str(ev.get("id"))) > after:
                out.append(ev)
        except ValueError:
            continue
    return out
