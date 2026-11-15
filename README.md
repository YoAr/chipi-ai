# Chipi

An AI business concierge: it finds, wins and tracks freelance work for one
person, and gets better with every reply. Hackathon build.

## How it fits together

```
Browser (React)  ⇄  Fly.io app (FastAPI + /data volume)  ←  Laptop (Claude Code routine)
                      ├─ /api/*     the UI's API
                      ├─ /api/chat  streamed Claude agent
                      ├─ /mcp       the same tools, for the routine
                      └─ /hooks/email  inbound replies
```

- The **Fly app is the only writer of data** (`/data`: wiki, events, SQLite).
- The **laptop routine** (Claude Code scheduled task / `/loop`) does the heavy
  agent work by calling the app's `/mcp` tools.
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

Not runnable yet. See `TODO.md` for the build order.
