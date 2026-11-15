# API contract

Both halves build against this. Every `GET` has a matching file in `fixtures/`
that the backend serves when `MOCK_MODE=1`. Change a shape only with both people
agreed, and update the fixture in the same commit.

All responses are JSON. Errors: `{"error": {"code": "…", "message": "…"}}`.

## UI API (`/api/*`)

| Method | Path | Body | Returns | Fixture |
|---|---|---|---|---|
| GET | `/api/state` | — | Header data for every screen | `state.json` |
| GET | `/api/persona` | — | Persona | `persona.json` |
| PUT | `/api/persona` | Persona fields | Persona | — |
| GET | `/api/fit` | — | Fits + rate with reasons | `fit.json` |
| GET | `/api/offers` | — | Three offers | `offers.json` |
| GET | `/api/leads?status=new` | — | Ranked leads | `leads.json` |
| POST | `/api/leads/{id}/verdict` | `{verdict: "useful"\|"maybe"\|"skip", note?}` | Lead | — |
| POST | `/api/seeds` | `{url}` | Lead (scored later) | — |
| GET | `/api/drafts?status=pending` | — | Drafts awaiting approval | `drafts.json` |
| POST | `/api/drafts/{id}/approve` | `{final_text}` | Draft (sent) | — |
| POST | `/api/drafts/{id}/reject` | `{note}` | Draft | — |
| GET | `/api/pipeline` | — | Columns + deadlines | `pipeline.json` |
| GET | `/api/metrics` | — | Headline numbers + groups | `metrics.json` |
| POST | `/api/chat` | `{messages: [{role, content}]}` | Server-sent events stream | — |
| POST | `/api/reset` | header `Authorization: Bearer $MCP_TOKEN` | `{ok: true}` | — |

### Shapes (field names are the contract; see fixtures for full examples)

```
Lead     { id, nickname, title, company, source, url, fetched: bool,
           rate_text, score: 0-100, why, status: new|useful|maybe|skip|drafted|sent|replied|won|lost,
           found_at }
Draft    { id, lead_id, nickname, channel: "email"|"apply_link", to, subject, body,
           verifier: { passed: bool, checks: [{name, passed, note}] },
           status: pending|approved|sent|rejected, created_at }
Rate     { floor, ask, low, high, currency: "USD", unit: "hour",
           reasons: [{ text, source }] }
Metric   { name, value, low, high, n, direction: "up"|"down" }   // low/high = range; n = sample size
```

### Chat stream (`POST /api/chat`)

Server-sent events, one JSON object per `data:` line:

```
{"type":"text","delta":"…"}
{"type":"tool","name":"suggest_rate","status":"running"|"done"}
{"type":"done"}
{"type":"error","message":"…"}
```

## Routine API (`/mcp`, token required)

The same tool functions the chat uses, plus queue tools:

`ask_wiki` · `ingest` · `lint` · `compute_pmf` · `suggest_rate` · `generate_offers` ·
`search_opportunities` · `score_leads` · `draft_outreach` · `verify_draft` ·
`get_metrics` · `record_lesson` · `get_queue` · `complete_queue_item`

## Inbound email (`POST /hooks/email`)

Resend inbound webhook. Appends a `reply_received` event and queues triage.
