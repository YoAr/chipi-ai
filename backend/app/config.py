"""Settings read from the environment (and .env at the repo root)."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    mock_mode: bool
    data_dir: Path
    fixtures_dir: Path
    mcp_token: str
    cors_origins: tuple[str, ...]
    # Pause between streamed chat chunks in mock mode; tests set it to 0.
    mock_stream_delay: float
    # Built React app (served at /app/) and the landing page (served at /)
    web_dist: Path = REPO_ROOT / "frontend" / "dist"
    landing_file: Path = REPO_ROOT / "frontend" / "landing" / "index.html"
    # OpenAI: the key itself is read by the SDK from OPENAI_API_KEY; we only note whether it is set.
    openai_key_set: bool = False
    model_smart: str = ""
    model_check: str = ""
    model_fast: str = ""
    # Demo passcode for changes (set on the public deploy only; empty = no passcode)
    demo_passcode: str = ""


def load_settings() -> Settings:
    return Settings(
        mock_mode=os.getenv("MOCK_MODE", "1") == "1",
        data_dir=Path(os.getenv("DATA_DIR", str(REPO_ROOT / "runtime-data"))),
        fixtures_dir=Path(os.getenv("FIXTURES_DIR", str(REPO_ROOT / "fixtures"))),
        mcp_token=os.getenv("MCP_TOKEN", ""),
        cors_origins=tuple(
            o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
        ),
        mock_stream_delay=float(os.getenv("MOCK_STREAM_DELAY", "0.03")),
        web_dist=Path(os.getenv("WEB_DIST", str(REPO_ROOT / "frontend" / "dist"))),
        landing_file=Path(os.getenv("LANDING_FILE", str(REPO_ROOT / "frontend" / "landing" / "index.html"))),
        openai_key_set=bool(os.getenv("OPENAI_API_KEY", "").strip()),
        model_smart=os.getenv("OPENAI_MODEL_SMART", "").strip(),
        model_check=os.getenv("OPENAI_MODEL_CHECK", "").strip(),
        model_fast=os.getenv("OPENAI_MODEL_FAST", "").strip(),
        demo_passcode=os.getenv("DEMO_PASSCODE", "").strip(),
    )
