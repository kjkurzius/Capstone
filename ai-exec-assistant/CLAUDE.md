# AI Executive Assistant — CLAUDE.md

Production AI Executive Assistant. Two subsystems sharing one codebase. **Claude Code builds it; n8n Cloud runs it.**

## Subsystems

**A — Executive Assistant** (runs in n8n Cloud, 24/7):
1. Polls an Outlook inbox via Microsoft Graph v1.0.
2. For emails unanswered > 4 hours, classifies urgency (Haiku) and drafts a reply (Sonnet).
3. **Draft-then-approve**: every reply is clearly labelled as AI and requires human approval before sending. We never auto-send.
4. Detects scheduling requests → checks calendar → proposes 2–3 slots → creates events **only after approval**.
5. Detects restaurant requests → generates a pre-filled OpenTable link (no consumer booking API exists).
6. Urgent items in real time; routine items in a twice-daily digest — both via a Power Automate Workflow webhook into Teams.

**B — Daily Opportunity Score** (runs in n8n Cloud on a Schedule Trigger at 06:00):
- Deterministic 0–100 score for Eagle Eye Innovations from countable inputs (Contracts Finder, Find a Tender, Companies House).
- Compares to the prior day, flags material changes, appends to history, delivers a summary in the digest.

## Hard rules

- **Model IDs live ONLY in `src/config/models.json`.** Never hardcode a model ID elsewhere. Sonnet-tier (`claude-sonnet-4-6`) for drafting/analysis; Haiku-tier (`claude-haiku-4-5`) for classification.
- **The Opportunity Score is deterministic.** `src/scoring/scoring-model.ts` computes it with pure math from countable API data. The LLM only interprets — it must never invent or alter the number. The day-over-day delta is therefore meaningful.
- **Draft-then-approve everywhere.** No email is sent and no calendar event is created without explicit human approval. Code paths that send/create are gated behind approval in the n8n workflows.
- **Use only real, documented APIs.** If a capability doesn't exist (e.g. OpenTable booking), generate a link instead — don't invent an API.
- **Teams uses the Power Automate Workflow webhook**, not the retired Office 365 Incoming Webhook connector.

## Verified external dependencies

- Microsoft Graph v1.0, OAuth2 delegated. Scopes: Mail.Read, Mail.ReadWrite, Mail.Send, Calendars.ReadWrite, User.Read, offline_access. KNOWN LIMITATION: delegated tokens can require periodic interactive re-auth — handled by `ReauthRequiredError` + a Teams alert in `graph-client.ts`.
- Anthropic Claude API (model IDs in `models.json`).
- Contracts Finder OCDS API — public, OGL, no key.
- Find a Tender OCDS API — OGL.
- Companies House Public Data API — free key, HTTP Basic (key as username).
- Teams via Power Automate "When a Teams webhook request is received".
- OpenTable — `restref` deep-link generation only.

## Layout

```
src/config/    models.json (model IDs), calendar-rules.json, scoring-weights.json
src/utils/     graph-client.ts, claude-client.ts, teams-notifier.ts, opentable-linker.ts
src/prompts/   urgency-classifier (Haiku), email-triage / calendar-extract / opportunity-summary (Sonnet)
src/scoring/   opportunity-score.ts (orchestrator), scoring-model.ts (deterministic math),
               report-generator.ts, sources/ (contracts-finder, find-a-tender, companies-house)
data/          scores/YYYY-MM-DD.json snapshots, score-history.json
n8n-spec/      node-by-node build specs for the five workflows (assemble in the n8n UI)
```

## Commands

- `npm run score` — run the scoring pipeline end-to-end against the live free APIs (add `--quiet` for composite+delta only, `--notify` to post to Teams).
- `npm run auth` — interactive Microsoft Graph sign-in (caches the delegated refresh token for local dev).
- `npm run test:graph` — `GET /me` smoke test.
- `npm test` — offline unit tests (deterministic scoring math, CPV detection, keyword matching, OpenTable links).
- `npm run typecheck` — TypeScript check.

## Dev vs production

`/loop` and this CLI are for development only — they need an open session / your machine on. The **n8n Schedule Trigger** is the unattended 24/7 runner. Build workflows from `n8n-spec/`, export the JSON yourself, and activate in n8n Cloud.
