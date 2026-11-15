# CLAUDE.md

Rules for writing code in this repo. Short on purpose; every rule matters.

## Product rules (never break these)

1. **Nothing is sent, accepted or priced without the user's approval.** Enforced
   in code at the send path, not only in prompts.
2. **Never below the rate floor** in `data/goal.md` (fixed core). Code check.
3. **Never invent a fact.** Every claim in a draft, rate or fit must trace to a
   file in `data/raw/` or a fetched job post. A gap is written as a gap.
4. **Email only to the allowlist** (`EMAIL_ALLOWLIST`) on the demo deploy.
5. **The fixed core of `data/goal.md` is human-owned.** Agents only rewrite the
   section marked DERIVED.

## Architecture rules

- **One writer.** Only the FastAPI app writes `/data`. The laptop routine changes
  data only through `/mcp` tools.
- **Write each tool once, expose it twice.** Tool functions live in
  `backend/tools/`; `/api/chat` uses them via the Anthropic Tool Runner, `/mcp`
  exposes them via the MCP SDK. No logic in the route handlers.
- **Events are append-only.** `data/events.jsonl` is never edited. Shape:
  `docs/events.md`.
- **Code runs instantly, Claude runs from the queue.** Event handlers do cheap,
  deterministic work inline; anything needing a model is queued for the routine.
- **Facts update instantly, beliefs at most daily.** Only the Learner writes
  `wiki/lessons.md` and the DERIVED goal.
- Single user: load data via `current_profile()` so users can be added later.

## Models

- `claude-opus-5-5`: judgment (fit, rate, offers, drafts, learning)
- `claude-sonnet-5-5`: verifier, wiki lint
- `claude-haiku-5-5`: bulk scoring, reply triage
- Thinking is adaptive (don't send `budget_tokens`). Stream long calls.

## Contract

`docs/contract.md` is the API both halves build against. Change it only with
both people agreed, and update `fixtures/` in the same commit.

## Secrets

`.env` locally, `fly secrets set` in prod. Never commit keys.
