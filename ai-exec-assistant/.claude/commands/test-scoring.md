---
description: Run the deterministic Opportunity Score pipeline against the live APIs and sanity-check the result.
---

Run and validate the Opportunity Score pipeline.

Steps:
1. Run `npm run score`.
2. Confirm the pipeline reached the live APIs. If any source returned `ok:false`, list the data-quality notes and explain that the score still computed but may be degraded (missing data treated as zero).
3. Sanity-check determinism: run `npm run score -- --quiet` a second time and confirm the composite is identical for the same inputs (the number must not move just because the LLM ran). Note: counts can change if the live API data changed between runs — distinguish "data changed" from "math is non-deterministic".
4. Verify a snapshot was written to `data/scores/YYYY-MM-DD.json` and that `data/score-history.json` has today's entry.
5. Confirm the printed COMPOSITE matches the `composite` in the snapshot and that the LLM summary quoted the same number (the model must never invent or alter it).
6. Report: composite, delta vs yesterday, whether it was flagged material (|delta| ≥ alert_threshold_delta), and which model generated the summary (llm vs fallback).

If `ANTHROPIC_API_KEY` is unset, note that the summary used the deterministic fallback — the score math is unaffected.
