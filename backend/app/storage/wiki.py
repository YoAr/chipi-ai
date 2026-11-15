"""Markdown wiki files under data_dir/wiki/, plus data_dir/goal.md and raw/ sources.

Paths are relative to the data dir, as wiki citations write them:
`read_page`/`write_page` accept "goal.md" or "wiki/<page>.md" (e.g.
"wiki/clients/acme.md") and nothing else. Anything that could escape (absolute
paths, "..", backslashes, NUL, symlinks pointing out) is refused.
"""

import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..mock_store import NotFound

GOAL = "goal.md"
WIKI_DIR = "wiki"
RAW_DIR = "raw"
LOG_PAGE = "log.md"

_log_lock = threading.Lock()
_raw_lock = threading.Lock()
_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class UnsafePath(ValueError):
    """A path that is absolute, contains '..' or backslashes, or leaves the allowed folders."""


def _resolve(data_dir: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel.strip():
        raise UnsafePath("Path is empty")
    if "\\" in rel or "\x00" in rel:
        raise UnsafePath(f"Refusing path {rel!r}: backslashes and NUL are not allowed")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise UnsafePath(f"Refusing absolute path {rel!r}")
    parts = PurePosixPath(rel).parts
    if any(p in ("..", ".", "") for p in rel.split("/")) or not parts:
        raise UnsafePath(f"Refusing path {rel!r}: '.' and '..' are not allowed")
    if not rel.endswith(".md"):
        raise UnsafePath(f"Refusing {rel!r}: wiki pages are .md files")

    root = Path(data_dir).resolve()
    if rel == GOAL:
        target, allowed = root / GOAL, root
    elif rel.startswith(WIKI_DIR + "/") and len(rel) > len(WIKI_DIR) + 1:
        allowed = root / WIKI_DIR
        target = allowed / rel[len(WIKI_DIR) + 1:]
    else:
        raise UnsafePath(f"Refusing {rel!r}: only goal.md and wiki/*.md pages are allowed")
    # Follow symlinks: the real target must still be inside the allowed folder
    real = target.resolve()
    if rel == GOAL:
        if real != root / GOAL:
            raise UnsafePath(f"Refusing {rel!r}: resolves outside the data dir")
    elif not real.is_relative_to(allowed.resolve()):
        raise UnsafePath(f"Refusing {rel!r}: resolves outside wiki/")
    return target


def read_page(data_dir: Path, rel: str) -> str:
    path = _resolve(data_dir, rel)
    if not path.is_file():
        raise NotFound(f"No page {rel}")
    return path.read_text(encoding="utf-8")


def write_page(data_dir: Path, rel: str, text: str) -> str:
    """Replace a page atomically. Returns the data-dir-relative path written."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    path = _resolve(data_dir, rel)
    if path.name == LOG_PAGE and path.parent.name == WIKI_DIR:
        raise UnsafePath("wiki/log.md is append-only; use append_log")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path.relative_to(Path(data_dir).resolve()).as_posix()


def append_log(data_dir: Path, kind: str, text: str) -> str:
    """Append "YYYY-MM-DD HH:MM · kind · text" (UTC) to wiki/log.md. Returns the line."""
    if not isinstance(kind, str) or not _KIND_RE.match(kind):
        raise ValueError(f"Bad log kind {kind!r}; use a short word like ingest, lesson, lint or run")
    one_line = " ".join(str(text).split())
    if not one_line:
        raise ValueError("Log text is empty")
    line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} · {kind} · {one_line}"
    path = Path(data_dir) / WIKI_DIR / LOG_PAGE
    path.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock:
        with path.open("a+", encoding="utf-8") as fh:
            fh.seek(0, os.SEEK_END)
            needs_newline = False
            if fh.tell() > 0:
                with path.open("rb") as rb:
                    rb.seek(-1, os.SEEK_END)
                    needs_newline = rb.read(1) != b"\n"
            fh.write(("\n" if needs_newline else "") + line + "\n")
    return line


def _safe_name(filename: str) -> str:
    name = str(filename or "").replace("\\", "/").split("/")[-1]
    name = name.replace("\x00", "")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:80] or "file"
    ext = re.sub(r"[^a-z0-9]+", "", ext.lower())[:10]
    return f"{stem}.{ext}" if ext else stem


def save_raw(data_dir: Path, filename: str, content: bytes) -> str:
    """Save a new source file as raw/YYYY-MM-DD-<safe-name>; never overwrites (adds -2, -3 …)."""
    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("content must be bytes")
    raw = Path(data_dir) / RAW_DIR
    raw.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(filename)
    stem, dot, ext = safe.rpartition(".")
    if not dot:
        stem, ext = safe, ""
    base = f"{datetime.now(timezone.utc):%Y-%m-%d}-{stem}"
    with _raw_lock:
        n = 1
        while True:
            name = f"{base}{'' if n == 1 else f'-{n}'}{'.' + ext if ext else ''}"
            try:
                fd = os.open(raw / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                n += 1
                continue
            with os.fdopen(fd, "wb") as fh:
                fh.write(bytes(content))
            return f"{RAW_DIR}/{name}"
