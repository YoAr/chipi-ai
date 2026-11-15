# AI pieces: what to build

Everything around these is done: routes, background jobs, status polling, storage,
the API contract (docs/contract.md → "AI actions"), and the real chat. Each piece
below is one file with its functions stubbed (`raise NotImplementedError`). Until a
piece is built, its job ends with the error "Not built yet: <function>", which the
UI can show.

## Ground rules (from CLAUDE.md)
- Call models only through `ai.structured(role, settings, instructions=..., input=..., schema=PydanticModel)`
  in `app/ai/client.py`. Roles: `"smart"` (gpt-6-astra), `"check"` (gpt-6.1-sol), `"fast"` (gpt-6-luna).
  Always write `ai.structured(...)` (not a direct import), so tests can monkeypatch it.
- Never invent facts. Use a bracketed placeholder (`[ONE RELEVANT PROJECT]`) for a gap.
- Rules that must hold are checked **in code**, not trusted to the model.
- Report progress with `jobs.step(store, key, step, message)`; messages are user-facing.
- Tests never call OpenAI: monkeypatch `app.ai.<module>.ai.structured`. See `tests/test_chat.py`
  for the fake-client pattern and `tests/test_ai_routes.py` for the route plumbing.

## Profile: `app/ai/profile.py` (job key `profile`)
Triggered by `POST /api/profile/build` after the CV upload.
1. `build_persona`: read the CV text (`settings.data_dir / store.get_setting("cv_text_path")`), extract
   name, role, years, location, skills, wants, avoids with the **smart** model. Extract only what the CV
   says; leave a field empty rather than guess. Save with `store.update_persona(...)`, keeping the user's
   hours and income goal. Write a cited summary to `wiki/persona.md`.
2. `build_fit`: persona + `goal.md` fixed core + `benchmarks.json` → `fixtures/fit.json` shape. Every
   reason cites `data/benchmarks.json#hourly`, `…#salary_equivalent`, or the CV text path.
   **Goal checks in code**: each fit has a source; `floor` equals goal.md's floor; floor ≤ low ≤ ask ≤ high;
   range within the benchmarks (+25% headroom); every cited source is allowed. On failure, retry up to twice
   with the failed checks as feedback; then save anyway with those checks `passed: false`.
   Save with `store.set_doc("fit", ...)` and write `wiki/rate.md`.
3. `build_offers`: three offers in `fixtures/offers.json` shape. No invented numbers in evidence.

## Search & scoring: `app/ai/scoring.py` (job key `search`)
`run_search` is done (fetch from RemoteOK, Remotive, HN, then score, then save). Build:
- `score_leads(leads, persona, fit, settings)`: one batched **fast** model call (up to 25 leads) returning
  `{id, score 0–100, why ≤ 140 chars}` grounded only in the post, combined with rule signals:
  final = round(0.6 × model + 0.4 × rules). Rules use `WEIGHTS` (skills overlap, rate fit, remote,
  avoided work, below the floor). **Leads below the floor are capped at 60 and their `why` says so.**
  If the model is unavailable, return the leads unscored (`score None`, why "Not scored: model unavailable")
  rather than failing.
- Tune `WEIGHTS` (marked `# TUNE:`). That's a product decision.

## Drafts & verifier: `app/ai/drafts.py` (job key `draft:<lead_id>`)
- `write_draft`: **smart** model. Under 150 words, one clear ask (a short call), quotes `fit.rate.ask`,
  mentions only experience in the persona. `channel` is `"email"` if the post contains an email address
  (used as `to`), else `"apply_link"` with `to` = the lead URL. No fit yet → `ValueError("Build your profile first")`.
- `code_checks`: Rate matches your ask · Not below your floor · Under 150 words, one ask · Channel allowed.
- `model_check`: a **separate check-model call**, fresh context: claims backed by the CV; professional tone.
- `run_draft`: write → check → if a code check fails, rewrite once with the failures as feedback →
  `store.add_draft({lead_id, nickname, channel, to, subject, body, verifier: {passed, checks}})` even if
  checks still fail (the user decides) → `jobs.done(..., result={"draft_id": id})`.

## Not in scope here
Sending email (the send path checks approval and the allowlist and is separate), the Learner,
and the laptop routine's `/mcp` tools.
