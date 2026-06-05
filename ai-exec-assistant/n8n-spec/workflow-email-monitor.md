# Workflow: Email Monitor (draft-then-approve)

**Goal:** every 30 minutes, find inbox messages received > 4 hours ago that are still unanswered, classify urgency (Haiku), draft a reply (Sonnet), and route it for human approval. Urgent → real-time Teams alert; routine → queued for the twice-daily digest. Detect scheduling / restaurant intent and hand off.

## Nodes (in order)

### 1. Schedule Trigger
- Type: **Schedule Trigger**
- Interval: every 30 minutes.

### 2. Compute cutoff (Code)
- Type: **Code** (Run Once)
- JS:
  ```js
  const cutoff = new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString();
  return [{ json: { cutoff } }];
  ```

### 3. Get messages (Microsoft Outlook)
- Type: **Microsoft Outlook** → Resource: *Message*, Operation: *Get Many*.
- Filter (OData `$filter`): `receivedDateTime le {{ $json.cutoff }} and isRead eq false`
- Fields/`$select`: `id,subject,from,receivedDateTime,bodyPreview,conversationId`
- Order by: `receivedDateTime desc`, Limit ~25.
- (Reference impl: `graph-client.ts` → `listRecentMessages`.)

> "Unanswered" heuristic: unread + older than 4h. For a stricter check, add a follow-up Outlook node that fetches the conversation and confirms no later message was sent by the user; skip those.

### 4. Split / loop
- Type: **Split In Batches** (or **Loop Over Items**) — process one message at a time.

### 5. Classify urgency (HTTP Request → Claude)
- Type: **HTTP Request**, POST `https://api.anthropic.com/v1/messages`
- Auth: Header Auth (Anthropic) + header `anthropic-version: 2023-06-01`.
- Body (JSON):
  ```json
  {
    "model": "={{ $vars.MODEL_CLASSIFY }}",
    "max_tokens": 512,
    "system": "<<contents of src/prompts/urgency-classifier.txt>>",
    "messages": [
      { "role": "user", "content": "Subject: {{ $json.subject }}\nFrom: {{ $json.from.emailAddress.address }}\n\n{{ $json.bodyPreview }}" }
    ]
  }
  ```
- Parse: read `content[0].text`, trim → one of `urgent|routine|ignore`.

### 6. IF ignore → stop
- Type: **IF** — if classification == `ignore`, end this item.

### 7. Draft reply (HTTP Request → Claude)
- Type: **HTTP Request**, POST `/v1/messages`, model `={{ $vars.MODEL_DRAFTING }}`, max_tokens 4000.
- `system` = contents of `src/prompts/email-triage.txt`.
- `user` = sender + subject + full body.
- Parse the `NOTES:` / `REPLY:` sections from `content[0].text`. Substitute `[USER_NAME]`/`[USER_PHONE]` in the signature from n8n vars.

### 8. Intent branch (Switch)
- Type: **Switch** on the `NOTES:` detected intent:
  - `scheduling` → call **workflow-calendar** (Execute Workflow node).
  - `restaurant` → call **workflow-restaurant** (Execute Workflow node).
  - else → continue to draft creation.

### 9. Create Outlook draft reply
- Type: **HTTP Request** (Graph) or **Microsoft Outlook** message node:
  - POST `/me/messages/{{ $json.id }}/createReply` → returns a draft message id.
  - PATCH `/me/messages/{draftId}` body: `{ "body": { "contentType": "HTML", "content": "<drafted reply + signature>" } }`.
- (Reference impl: `graph-client.ts` → `createReplyDraft` + `updateMessage`.) **Do not send here.**

### 10. Approval gate
- Type: **Send and Wait for Approval** (or a Teams Adaptive Card with Approve/Reject actions posted via the Power Automate URL that calls back to an n8n Webhook).
- Present: sender, subject, the drafted reply, and the draft message id.

### 11. IF approved → Send
- Type: **IF** approved →
  - **Microsoft Outlook** *Send* the draft (`POST /me/messages/{draftId}/send`).
  - else: leave the draft in the Drafts folder for manual editing.

### 12. Route notification
- **urgent** path → **HTTP Request → Teams** (real-time alert; payload per `teams-notifier.ts` `sendTeamsAlert`).
- **routine** path → write the item to a store the digest reads (n8n Data Store / a Google Sheet / a Postgres table / static data), so `workflow-daily-digest` can batch it. Do not alert in real time.

## Invariants
- No send before the approval gate (step 10/11).
- Haiku for classify, Sonnet for draft — IDs from n8n vars mirroring `models.json`.
- Signature appended to every draft.
