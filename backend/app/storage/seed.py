"""First-boot seeding: copy the repo's data/ folder into the runtime data dir."""

import shutil
from pathlib import Path

# goal.md is copied last: it is the "already seeded" marker, so an interrupted
# seed is finished on the next start instead of being skipped.
SEED_FILES = ("AGENTS.md", "benchmarks.json", "events.jsonl")
SEED_DIRS = ("wiki", "raw")
MARKER = "goal.md"
_SKIP = {".gitkeep", ".DS_Store"}


def _copy_missing(src: Path, dst: Path) -> None:
    if src.name in _SKIP or src.is_symlink():
        return
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            _copy_missing(child, dst / child.name)
    elif src.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def ensure_seeded(data_dir: Path, seed_dir: Path) -> bool:
    """Seed data_dir from seed_dir unless data_dir/goal.md exists. Never overwrites a file.

    Returns True if seeding ran.
    """
    data_dir, seed_dir = Path(data_dir), Path(seed_dir)
    if (data_dir / MARKER).exists():
        return False
    if not (seed_dir / MARKER).is_file():
        raise FileNotFoundError(f"Seed folder {seed_dir} has no {MARKER}")
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in SEED_DIRS:
        (data_dir / name).mkdir(parents=True, exist_ok=True)
        if (seed_dir / name).is_dir():
            _copy_missing(seed_dir / name, data_dir / name)
    for name in SEED_FILES:
        if (seed_dir / name).is_file():
            _copy_missing(seed_dir / name, data_dir / name)
    _copy_missing(seed_dir / MARKER, data_dir / MARKER)
    return True
