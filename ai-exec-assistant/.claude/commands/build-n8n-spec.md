---
description: Regenerate / validate the node-by-node n8n build specs from the current code.
---

Keep the `n8n-spec/` markdown in sync with the standalone code so the n8n UI build matches the reference implementation.

Do NOT hand-author n8n workflow JSON — blindly generated workflow JSON usually fails to import. Produce precise node-by-node specs instead.

Steps:
1. Read the current request shapes from `src/utils/graph-client.ts` (mail/calendar/sendMail), `src/utils/teams-notifier.ts` (Teams payload), `src/utils/claude-client.ts` + the `src/prompts/*.txt` (Claude calls), and `src/scoring/*` (scoring pipeline).
2. For each of the five workflows, confirm the spec lists: the trigger node + settings, every node in order with its precise n8n node type and key parameters, the exact HTTP request bodies for Graph/Claude/Teams calls, and the connections between nodes. Where an n8n built-in node exists (Microsoft Outlook, Schedule Trigger, HTTP Request, IF, Code), name it precisely.
3. Flag any drift: if a request body or model reference in the code differs from the spec, update the spec markdown to match the code.
4. Re-assert the invariants in each spec: draft-then-approve gates before any send/create node; deterministic scoring (the Code node runs the same math, the LLM node only interprets); Power Automate Workflow webhook for Teams (never the retired Incoming Webhook).
5. Summarize what changed and remind the user to assemble/export the JSON themselves in the n8n UI.
