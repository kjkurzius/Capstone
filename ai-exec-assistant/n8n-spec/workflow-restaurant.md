# Workflow: Restaurant (OpenTable link generation)

**Goal:** when an email is flagged as a restaurant/booking request, extract the details and generate a **pre-filled OpenTable link** for the human to complete. OpenTable has no consumer booking API — we generate links only; we never claim a booking was made.

Invoked by `workflow-email-monitor` (Execute Workflow node).

## Nodes (in order)

### 1. Trigger
- **Execute Workflow Trigger** — input: sender, subject, body.

### 2. Extract booking details (HTTP Request → Claude)
- POST `/v1/messages`, model `={{ $vars.MODEL_DRAFTING }}` (Sonnet), max_tokens 800.
- `system`: instruct strict JSON extraction:
  ```
  Extract restaurant booking intent. Return strict JSON only:
  { "is_restaurant_request": boolean, "restaurant_name": string, "opentable_rid": string,
    "date": "YYYY-MM-DD" | "", "time": "HH:MM" | "", "covers": number, "notes": string }
  Never invent an rid. Leave fields "" if not stated. covers defaults to 2 if unstated.
  ```
- `user`: sender + subject + body. Parse `content[0].text` as JSON.

### 3. IF not a restaurant request → return
- **IF** `is_restaurant_request == false` → return control to caller.

### 4. Build OpenTable link (Code)
- Type: **Code**. Mirror `src/utils/opentable-linker.ts`:
  - If `opentable_rid` present and `date` + `time` present:
    ```js
    const dt = `${date}T${time}`; // YYYY-MM-DDTHH:MM (local)
    const url = `https://www.opentable.com/restref/client/?rid=${rid}&datetime=${dt}&covers=${covers}`;
    ```
  - Else fall back to a search link: `https://www.opentable.com/s?term={{ encodeURIComponent(restaurant_name) }}`.

### 5. Draft reply with the link (HTTP Request → Claude or Code)
- Compose a short reply that includes the OpenTable link and states clearly that this is a **pre-filled link to complete the booking** (not a confirmed reservation). Append the AI signature.

### 6. Approval gate + send
- **Send and Wait for Approval**, then send via the email-monitor's send path (Outlook send of the draft) only if approved. Alternatively, surface the link to the executive via the Teams digest.

## Invariants
- Never assert a reservation was made — we only generate a link.
- `rid` is never invented; without it, use the search-link fallback.
- Reply is approval-gated like all outbound mail.
