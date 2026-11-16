# CLAUDE.md

Rules for writing code in this repo. Short on purpose; every rule matters.

## Product rules (never break these)

1. **Nothing is sent, accepted or priced without the user's approval.** Enforced
   in code at the send path, not only in prompts.
2. **Never below the rate floor** in the freelancer's **approved profile** (EUR/hour,
   `app.profiles.rate_floor`). Code check (`guards.check_rate`); with no approved profile,
   nothing is priced.
3. **Never invent a fact.** Every claim in a draft, rate or fit must trace to a
   file in `data/raw/` or a fetched job post. A gap is written as a gap.
4. **Email only to the allowlist** (`EMAIL_ALLOWLIST`) on the demo deploy.
5. **The fixed core of `data/goal.md` is human-owned.** Agents only rewrite the
   section marked DERIVED.

## Architecture rules

- **One writer.** Only the FastAPI app writes `/data`. The laptop routine changes
  data only through `/mcp` tools.
- **Write each tool once, expose it twice.** Tool functions live in
  `backend/tools/`; `/api/chat` offers them as function tools through the OpenAI
  Responses API, `/mcp` exposes them via the MCP SDK. No logic in the route handlers.
- **Events are append-only.** `data/events.jsonl` is never edited. Shape:
  `docs/events.md`.
- **Code runs instantly, the model runs from the queue.** Event handlers do cheap,
  deterministic work inline; anything needing a model is queued for the routine.
- **Facts update instantly, beliefs at most daily.** Only the Learner writes
  `wiki/lessons.md` and the DERIVED goal.
- Single user: load data via `current_profile()` so users can be added later.

## Models

All model calls go to **OpenAI** through the official `openai` Python SDK and the
**Responses API** (not Chat Completions; the Assistants API is retired). Model IDs
are settings, never hard-coded; confirm them against `GET /v1/models` with your key.

| Setting | Role | Used for |
|---|---|---|
| `OPENAI_MODEL_SMART` | Flagship | Fit, rate, offers, drafts, learning, chat |
| `OPENAI_MODEL_CHECK` | Mid tier | Verifier, wiki lint |
| `OPENAI_MODEL_FAST` | Smallest | Bulk lead scoring, reply triage |

- Tools: function tools with strict JSON schemas, plus OpenAI's built-in web search tool.
- Typed outputs: `client.responses.parse(...)` with a Pydantic model; never parse free text.
- The verifier is a separate call with fresh context; the model that wrote a draft never grades it.
- Stream long calls (chat, drafts) so requests don't time out.

## Contract

`docs/contract.md` is the API both halves build against. Change it only with
both people agreed, and update `fixtures/` in the same commit.

## Secrets

`.env` locally, `fly secrets set` in prod. Never commit keys.
