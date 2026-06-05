---
description: Verify Microsoft Graph delegated auth — refresh the token and call GET /me.
---

Verify the Microsoft Graph delegated OAuth setup end to end.

Steps:
1. Confirm `.env` has real (non-placeholder) values for `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID`, `MICROSOFT_REDIRECT_URI`. If any is still a `[PLACEHOLDER]`, stop and tell the user what to fill in.
2. If no token cache exists yet (`.graph-token-cache.json`), instruct the user to run `npm run auth` and complete the interactive sign-in first.
3. Run `npm run test:graph` (which calls `GET /me`). 
4. Report the signed-in display name and email on success.
5. On failure:
   - If it's a `ReauthRequiredError`, explain that the delegated refresh token expired or was revoked (conditional access / password change) and that the fix is to re-run `npm run auth`. Note that a Teams re-auth alert should have fired if `TEAMS_WORKFLOW_URL` is set.
   - Otherwise surface the exact Graph error (status + body) and the most likely cause (wrong scopes, admin consent not granted, redirect URI mismatch).

Do not print secrets. Summarize the result clearly.
