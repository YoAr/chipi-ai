# Wiki rules

The agent's memory, kept as plain markdown.

- `raw/` holds sources: resume, interview transcripts, job posts, replies.
  **Never edit a raw file.** Add new ones; name them `YYYY-MM-DD-<slug>.<ext>`.
- `wiki/` is agent-owned. Every claim on a wiki page cites a raw file or an
  event id, like `(raw/2026-11-15-interview.md)` or `(ev_0150)`.
- `wiki/index.md` lists every page with a one-line summary. Read it first.
- `wiki/log.md` gets one line per ingest, lesson or lint pass. Append only.
- `wiki/lessons.md`: one entry per lesson with id, date, evidence, and an
  `[applied <date> → <what changed>]` stamp. Pruned lessons are marked, not deleted.
- A lesson needs the user's own words, or at least 5 data points on each side and
  80% confidence.
- Only the Learner edits `lessons.md` and the DERIVED part of `../goal.md`.

## Pages
`persona.md` · `pmf.md` · `rate.md` · `offers.md` · `channels.md` · `voice.md` ·
`lessons.md` · `clients/<nickname>.md`
