# Workflow: Opportunity Score (daily 06:00, deterministic)

**Goal:** every day at 06:00, compute the deterministic Opportunity Score for Eagle Eye Innovations from countable API inputs, compare to yesterday, have the LLM interpret (never invent) the number, store the snapshot + history, and make it available to the daily digest. **This Schedule Trigger is what makes the system true 24/7.**

This mirrors `src/scoring/opportunity-score.ts`. The math lives in a **Code** node (mirror of `scoring-model.ts`); the LLM only writes prose.

## Nodes (in order)

### 1. Schedule Trigger
- Type: **Schedule Trigger** — daily at 06:00 `USER_TIMEZONE`.

### 2. Load config (Code)
- Hold `scoring-weights.json` as a node constant (keyword query, weights, normalization, competitor list, target CH number `08631843`, `alert_threshold_delta`).

### 3a. Contracts Finder (HTTP Request)
- GET `https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?publishedFrom={{from}}&publishedTo={{today}}&limit=100`
- Header `Accept: application/json`. No auth. (Reference: `sources/contracts-finder.ts`.)

### 3b. Find a Tender (HTTP Request)
- GET `https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?updatedFrom={{fromIso}}&updatedTo={{nowIso}}&limit=100`
- Header `Accept: application/json`. No auth. (Reference: `sources/find-a-tender.ts`.)

### 3c. Companies House — company health (HTTP Request)
- GET `https://api.company-information.service.gov.uk/company/08631843`
- Auth: **Basic Auth** credential (username = CH API key, password blank). (Reference: `sources/companies-house.ts`.)

### 3d. Companies House — competitor filings (HTTP Request, per competitor)
- For each number in `competitor_company_numbers`: GET `/company/{num}/filing-history?items_per_page=100`, count filings in last 30 days. (Skip/neutral if the list is empty.)

> Run 3a–3d in parallel branches, then **Merge** before step 4. Each branch should tolerate errors (Continue On Fail) and emit an `ok` flag + note, so a single API outage degrades rather than breaks the run.

### 4. Derive countable inputs (Code)
- Filter notices to the last 7 days matching the keyword query (OR terms).
- Compute: `new_relevant_tenders` (count), `tender_value_signal` (sum of GBP values), `defense_security_notices` (count with CPV prefix in `["35"]`), `competitor_filings` (count), `company_health` (status + adverse flags).

### 5. Compute score (Code) — DETERMINISTIC
- Mirror `scoring-model.ts`: normalize each metric per its config (`linear` / `inverse_linear` / `log` / `health_flags`), multiply by weight × scale, sum → `composite` (0–100). **No LLM here.** Same inputs ⇒ same number.

### 6. Compare to yesterday (Code + datastore read)
- Read the previous composite from the score datastore / history. `delta = composite − previous`. `material = |delta| ≥ alert_threshold_delta`.

### 7. Interpret (HTTP Request → Claude, Sonnet)
- POST `/v1/messages`, model `={{ $vars.MODEL_DRAFTING }}`, max_tokens 4000.
- `system` = contents of `src/prompts/opportunity-summary.txt` (explicitly: the score is already computed; quote it, never recompute).
- `user` = the facts block (composite, previous, delta, category breakdown, recent notices, data-quality notes). (Reference: `report-generator.ts` `buildFactsBlock`.)
- Parse `content[0].text` as the prose summary.

### 8. Persist
- Write a snapshot row/object (`date`, `composite`, `delta`, `material`, `inputs`, `categories`, `notices`, `summary`) to the score datastore, and update the history with today's `{date, composite}`.

### 9. Alert if material
- **IF** `material` → **HTTP Request → Teams** (Power Automate Workflow URL) with `sendTeamsAlert` payload, flagged urgent.
- Always: the daily digest (`workflow-daily-digest`) reads the stored summary for its Opportunity Score section.

## Invariants
- Score computed by the Code node (deterministic math) — the LLM only interprets and must echo the number.
- All source fetches degrade gracefully; degraded data is surfaced in the summary, not silently dropped.
- `08631843` is the ASSUMED EEI Companies House number — verify the entity before relying on `company_health`.
