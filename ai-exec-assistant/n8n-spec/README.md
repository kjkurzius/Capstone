# n8n Build Specs

These are **node-by-node build specifications**, not importable workflow JSON. Claude Code does **not** hand-author n8n workflow JSON — blindly generated JSON usually fails to import. Assemble each workflow in the n8n Cloud UI from the spec, then export the JSON yourself.

## Why n8n runs this (not `/loop` or Claude Desktop)

The Opportunity Score and the inbox poll must run unattended, every day, regardless of whether any laptop is on or any Claude Code session is open. Only n8n Cloud (or a VPS cron) is genuinely 24/7. `/loop` needs an open session and expires after 3 days; Desktop tasks need your machine on. **Claude Code builds these; the n8n Schedule/poll triggers run them.**

## Prerequisites (see ../STEP 3 in the master prompt / project README)

1. **Entra ID app registration** with the six delegated Graph permissions + admin consent. Production redirect URI = your n8n Cloud OAuth callback.
2. **Companies House** free API key.
3. **Power Automate Workflow** for Teams — "Post to a channel when a webhook request is received" → copy the HTTP URL.
4. **n8n Cloud** (Pro) — connect Microsoft 365 credentials, set credentials/env for Anthropic + Companies House + the Teams URL.

## Credentials to create in n8n

| n8n credential | Used by | Notes |
|---|---|---|
| Microsoft Outlook OAuth2 | mail + calendar nodes | Delegated; same scopes as `graph-client.ts`. n8n manages token refresh. |
| Header Auth — Anthropic | HTTP Request → Claude | Header `x-api-key: <ANTHROPIC_API_KEY>`, plus `anthropic-version: 2023-06-01`. |
| Basic Auth — Companies House | HTTP Request → CH | Username = API key, password = blank. |
| (env) Teams Workflow URL | HTTP Request → Teams | Store the Power Automate URL as an n8n variable/credential. |

## Model IDs

Every Claude HTTP node uses the IDs from `src/config/models.json`:
- drafting/analysis: `claude-sonnet-4-6`
- classification: `claude-haiku-4-5`

Keep these in one n8n variable each (e.g. `MODEL_DRAFTING`, `MODEL_CLASSIFY`) so they update in one place — mirroring the single-source rule in code.

## The five workflows

| File | Trigger | Purpose |
|---|---|---|
| `workflow-email-monitor.md` | Schedule (every 30 min) | Find >4h-unanswered mail, classify, draft, route for approval. |
| `workflow-calendar.md` | Called by email-monitor (or sub-workflow) | Extract scheduling intent, propose slots, create event after approval. |
| `workflow-restaurant.md` | Called by email-monitor | Detect restaurant request, generate OpenTable link. |
| `workflow-daily-digest.md` | Schedule (twice daily) | Batch routine items + latest Opportunity Score into one Teams digest. |
| `workflow-opportunity-score.md` | Schedule (06:00 daily) | Deterministic score: fetch → compute → compare → report → store. |

## Invariants every workflow must preserve

- **Draft-then-approve.** No Send Mail / Create Event node runs before a human-approval gate (n8n "Wait" / "Send and Wait for approval" / a Teams Adaptive Card action that posts back).
- **Deterministic scoring.** The score is computed in a **Code** node running the same math as `scoring-model.ts`. The Claude node only writes prose and must echo the number.
- **Teams via Power Automate Workflow** webhook — never the retired Office 365 Incoming Webhook connector.
- **AI signature** appended to every drafted reply.
