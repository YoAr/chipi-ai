# How we know it's right

| Layer | When | Blocks? | Examples |
|---|---|---|---|
| 1. Code checks | Every action | Yes | Shape valid · rate ≥ floor · send has approval · post fetched · not a duplicate · allowlisted recipient |
| 2. Verifier (fresh context, `OPENAI_MODEL_CHECK`) | Every draft | Yes, or flags | Claims trace to `raw/` · one clear ask · matches `voice.md` · rate matches `rate.md` |
| 3. Labelled sets (`evals/`) | Every prompt or model change | Before merge | Scorer: 20 posts · Triage: 20 replies · Writer: 5 leads, rubric |
| 4. Learner simulation | Every Learner change | Before merge | Finds a real pattern; does **not** invent one from noise |
| 5. Live signals | Always | No | Every skip, edit or reversal becomes a labelled example |

Rules:
- The model that wrote something never grades it.
- Judge rubrics are yes/no items, not 1–10 scores.
- Unsure triage goes to the user. Missing an interested reply costs more than a notification.
- Learner changes over 10% (rate) or dropping a channel are proposals the user approves.
