# Chipi

An AI business concierge: it finds, wins and tracks freelance work for one
person, and gets better with every reply. Hackathon build.

## How it fits together

```
Browser (React)  ⇄  Fly.io app (FastAPI + /data volume)  ←  Laptop routine (Claude Code or Codex)
                      ├─ /api/*     the UI's API
                      ├─ /api/chat  streamed agent (OpenAI Responses API)
                      ├─ /mcp       the same tools, for the routine
                      └─ /hooks/email  inbound replies
```

- The **Fly app is the only writer of data** (`/data`: wiki, events, SQLite).
- **Models: OpenAI** (see `CLAUDE.md` → Models). All model calls happen inside the
  app's tools, so the routine never needs its own model key.
- The **laptop routine** (a scheduled task in Claude Code or Codex) is only a timer:
  it calls the app's `/mcp` tools, which do the work.
- **Single user, no login.** Guardrails instead: MCP token, API spend cap, chat
  rate limit, email allowlist.

## Repo layout

| Path | What |
|---|---|
| `CLAUDE.md` | Rules for anyone (or any agent) writing code here |
| `TODO.md` | Build plan, owners, real vs faked for the demo |
| `docs/contract.md` | The API both halves build against |
| `docs/events.md` | Event types and shape |
| `docs/evals.md` | How we check things are right |
| `data/` | Seed data dir (copied to `/data` on the volume): goal, wiki, raw, events |
| `fixtures/` | Sample API responses: mock server, demo mode, tests |
| `backend/` | FastAPI app (not written yet) |
| `frontend/` | React app (not written yet) |
| `evals/` | Labelled test sets + scorer script (not written yet) |

## Run locally

The backend runs in **mock mode**: every `/api/*` route serves `fixtures/`, and
writes (verdicts, approvals, seeds) change an in-memory copy so the UI can click
through. `POST /api/reset` or a restart restores the fixtures.

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8010
```

- Landing page: http://localhost:8010/ · app: http://localhost:8010/app/ (after `npm run build` in `frontend/`)
- API: http://localhost:8010/api/state · interactive docs: http://localhost:8010/docs
- Tests: `uv run pytest -q`
- Port 8010, because 8000 is often taken by other Docker projects.

Frontend with hot reload (calls the backend through Vite's `/api` proxy):

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173/app/
```

## Landing page

`frontend/landing/chipi-homepage.export.html` is the design export as delivered.
`python3 scripts/build_landing.py` writes `frontend/landing/index.html` (served at
`/`) with its "Upload your CV" and "Log in" links pointing at the app. Re-run it
after saving a new export over the old one.

## Deploy (Fly.io)

One machine serves the landing page, app and API (see `Dockerfile`, `fly.toml`).
Data lives on a Fly volume mounted at `/data` (`DATA_DIR=/data`).

```bash
brew install flyctl
fly auth login
fly launch --no-deploy --copy-config --name chipi-ai   # first time only
# Create the data volume BEFORE the first deploy that has [mounts] in fly.toml
# (one machine, region iad):
fly volumes create chipi_data --region iad --size 1 -a chipi-ai
```

Deploys happen by pushing to `main`: Fly's GitHub integration builds and deploys
the root `Dockerfile`. CI (`.github/workflows/ci.yml`: backend tests, frontend
build, docker build) must be green before merging.

## Operations

**Secrets.** Set in the Fly dashboard: app `chipi-ai` → Secrets → Deploy secrets
(they are available to the build and runtime, so GitHub-integration deploys get them).

| Secret | Required | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | yes | All model calls |
| `MCP_TOKEN` | yes | Bearer token for `/mcp` and `POST /api/reset` |
| `RESEND_API_KEY` | optional | Sending email |
| `EMAIL_ALLOWLIST` | optional | Comma-separated recipients the demo may email |

**Snapshots and restore.** Fly snapshots the volume daily; take one before risky changes.

```bash
fly volumes list -a chipi-ai
fly volumes snapshots list <volume-id> -a chipi-ai
fly volumes snapshots create <volume-id> -a chipi-ai
# Restore: create a new volume from a snapshot, then point the machine at it
fly volumes create chipi_data --region iad --snapshot-id <snapshot-id> -a chipi-ai
```

**Logs.** `fly logs -a chipi-ai`, or dashboard → Monitoring.

**Reset to seed data.** `POST /api/reset` with the MCP token:

```bash
curl -X POST https://chipi-ai.fly.dev/api/reset -H "Authorization: Bearer $MCP_TOKEN"
```
