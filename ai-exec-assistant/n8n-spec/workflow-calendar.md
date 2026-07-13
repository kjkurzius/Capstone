# Workflow: Calendar (propose slots → approve → create event)

**Goal:** given an email flagged as a scheduling request, extract the intent, check the calendar, propose 2–3 slots that respect `calendar-rules.json`, and create the event **only after approval**.

Invoked by `workflow-email-monitor` (Execute Workflow node) with the message context, or standalone for testing.

## Nodes (in order)

### 1. Trigger
- **Execute Workflow Trigger** (called by email-monitor) — input: sender, subject, body, conversationId.

### 2. Extract scheduling intent (HTTP Request → Claude)
- POST `/v1/messages`, model `={{ $vars.MODEL_DRAFTING }}` (Sonnet), max_tokens 1000.
- `system` = contents of `src/prompts/calendar-extract.txt`.
- `user` = sender + subject + body.
- Parse `content[0].text` as **strict JSON** → `{ is_scheduling_request, meeting_purpose, with, duration_minutes, modality, earliest_date, latest_date, preferred_times, notes }`.

### 3. IF not a scheduling request → return
- **IF** `is_scheduling_request == false` → return control to caller (fall back to a normal drafted reply).

### 4. Compute search window (Code)
- Read `calendar-rules.json` (paste as a node parameter or load via a Code node constant).
- Build `startDateTime` / `endDateTime` for the next `lookahead_business_days`, honouring `earliest_date`/`latest_date` if present.

### 5. Get calendar view (Microsoft Outlook / HTTP Graph)
- GET `/me/calendarView?startDateTime={{start}}&endDateTime={{end}}&$select=subject,start,end&$orderby=start/dateTime`
- (Reference impl: `graph-client.ts` → `listCalendarEvents`.)

### 6. Propose slots (Code)
- Type: **Code**. Pure logic — no LLM. **Mirror `src/utils/calendar-planner.ts` `proposeSlots()`** (the local runner uses the same function, so keep them in sync). Using `calendar-rules.json`:
  - Iterate business days in the window; within `working_hours`, skip the `lunch_block`, respect `no_meeting_before`/`no_meeting_after`.
  - Exclude times overlapping existing events ± `buffer_minutes_between_meetings`.
  - If `avoid_back_to_back`, drop slots adjacent to an existing event.
  - Emit between `min_slots_to_propose` and `max_slots_to_propose` slots of `duration_minutes` (default from intent or rules).

### 7. Draft proposal reply (HTTP Request → Claude, optional)
- Sonnet drafts a short reply offering the proposed slots (or build it deterministically in the Code node). Append the AI signature.

### 8. Approval gate
- **Send and Wait for Approval** — present the proposed slots + the email context. The approver picks a slot (or rejects).

### 9. IF approved → Create event
- **Microsoft Outlook** *Create Event* (or HTTP `POST /me/events`):
  ```json
  {
    "subject": "{{ $json.meeting_purpose }}",
    "start": { "dateTime": "{{ chosenSlot.start }}", "timeZone": "{{ $vars.USER_TIMEZONE }}" },
    "end":   { "dateTime": "{{ chosenSlot.end }}",   "timeZone": "{{ $vars.USER_TIMEZONE }}" },
    "attendees": [{ "emailAddress": { "address": "{{ senderEmail }}" }, "type": "required" }]
  }
  ```
  - (Reference impl: `graph-client.ts` → `createEvent`.)
- else: send only the proposal reply (still via the approval-gated send), no event created.

## Invariants
- No event is created before approval (step 8/9).
- Slot proposal is deterministic (Code node), governed solely by `calendar-rules.json`.
- All times carry `USER_TIMEZONE`.
