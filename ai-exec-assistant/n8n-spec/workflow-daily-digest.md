# Workflow: Daily Digest (twice daily)

**Goal:** twice a day, batch the queued routine email items and the latest Opportunity Score into a single Teams digest card via the Power Automate Workflow webhook.

## Nodes (in order)

### 1. Schedule Trigger
- Type: **Schedule Trigger** — two times daily (e.g. 08:30 and 16:30 `USER_TIMEZONE`). Use two trigger rules or a cron expression.

### 2. Read queued routine items
- Type: **n8n Data Store / Postgres / Google Sheets** read (whatever `workflow-email-monitor` writes routine items to).
- Pull items since the last digest; mark them as sent afterwards (step 7).

### 3. Read latest Opportunity Score
- Type: **Read** the latest score. Options:
  - If the scoring workflow writes to the same datastore, read today's row; **or**
  - Read the latest `data/scores/YYYY-MM-DD.json` if scoring runs in the same n8n instance with filesystem access; **or**
  - Call a small sub-workflow that returns the cached score.
- Extract `composite`, `delta`, `material`, and `summary` (the LLM prose).

### 4. Build digest (Code)
- Compose a Markdown body:
  - Header: `Daily digest — {{ count }} routine items · Opportunity Score {{ composite }}/100 ({{ delta }})`.
  - Section 1: list routine email items (sender, subject, one-line draft status / link to the draft).
  - Section 2: the Opportunity Score `summary` prose (already generated; do not re-summarize the number).

### 5. Post to Teams (HTTP Request → Power Automate Workflow)
- Type: **HTTP Request**, POST to `={{ $vars.TEAMS_WORKFLOW_URL }}`.
- Body: the Adaptive Card payload from `teams-notifier.ts` `sendDigest` (a `message` object with an `attachments[0]` Adaptive Card; include top-level `summary`/`text` mirrors).
- Expect `202 Accepted`.

### 6. (No real-time alerts here)
- Urgent items were already alerted in real time by `workflow-email-monitor`. The digest is routine-only.

### 7. Mark items as digested
- Update the datastore so the next digest doesn't repeat them.

## Invariants
- Teams via the **Power Automate Workflow** webhook (never the retired Incoming Webhook).
- The digest quotes the deterministic score/delta as-is — it does not recompute.
