# SETUP — connecting the AI Executive Assistant

Two ways to run this:

- **Local (dev / verification):** the `npm` scripts run on your machine. Good for proving auth, the scoring pipeline, and Teams posting work before you touch n8n.
- **Production (24/7):** n8n Cloud runs the five workflows on schedules. Local is *not* the runner — your machine would have to stay on.

Do the local path first; it validates every credential. Then replicate the credentials into n8n.

---

## 0. Get the code on your machine

```bash
git clone <this repo> && cd Capstone/ai-exec-assistant
npm install
cp .env.example .env      # you'll fill this in as you collect credentials below
```

Replace **every** `[PLACEHOLDER]` in `.env` as you go.

---

## 1. Microsoft Entra app registration (Graph: mail + calendar)

Done once by someone with Entra admin rights.

1. https://entra.microsoft.com → **App registrations** → **New registration**.
2. Name it (e.g. "AI Exec Assistant"). Supported account types: **single tenant** (your org only).
3. **Redirect URI** → platform **Web**:
   - For local dev add: `http://localhost` (Entra allows plain http for localhost).
   - For production add your **n8n OAuth callback** (n8n shows this exact URL when you create the Microsoft credential — see step 6). You can list both.
4. **Register**, then from **Overview** copy:
   - Application (client) ID → `MICROSOFT_CLIENT_ID`
   - Directory (tenant) ID → `MICROSOFT_TENANT_ID`
5. **Certificates & secrets** → **New client secret** → copy the **Value** (not the ID) → `MICROSOFT_CLIENT_SECRET`. (It's only shown once.)
6. **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions** → add all six:
   `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `User.Read`, `offline_access`
   → then **Grant admin consent** (the button at the top). Without consent every Graph call 403s.
7. Set `MICROSOFT_REDIRECT_URI=http://localhost` for local dev (must match what you registered).

> **Known limitation (by design):** delegated refresh tokens expire / can be revoked (conditional access, password change, ~90 days idle). When that happens the client throws `ReauthRequiredError` and fires a Teams alert; the fix is re-running `npm run auth`. There is no fully unattended delegated auth — interactive sign-in is required to re-mint the token.

---

## 2. Anthropic API key

https://console.anthropic.com → API keys → create → `ANTHROPIC_API_KEY`.
(Models are pinned in `src/config/models.json`: `claude-sonnet-4-6` for drafting, `claude-haiku-4-5` for classification — no need to set them in `.env`.)

---

## 3. Companies House key (free)

https://developer.company-information.service.gov.uk → register → **create an application** (REST) → copy the key → `COMPANIES_HOUSE_API_KEY`.
Auth is HTTP Basic with the key as the username and a blank password (handled in code).

> Verify `08631843` is actually Eagle Eye Innovations before trusting `company_health` — search the entity on the same site.

---

## 4. Teams via Power Automate Workflow

The old Office 365 Incoming Webhook connector is retired — use **Workflows**.

1. In Teams: **Apps** → **Workflows** → template **"Post to a channel when a webhook request is received"**.
2. Pick the team + channel for alerts/digests → create.
3. Copy the generated **HTTP POST URL** → `TEAMS_WORKFLOW_URL`.

---

## 5. Local verification (do this before n8n)

```bash
npm run auth          # opens the Entra sign-in URL; paste the redirected URL back
npm run test:graph    # GET /me — prints your name/email on success
npm test              # offline unit tests (scoring math, links, CPV) — should be 16/16
npm run score         # full pipeline against the live free APIs
npm run score -- --notify   # also posts the digest to your Teams channel
```

If `npm run score` shows 403s on Contracts Finder / Find a Tender from *your* machine (not this sandbox), check your network/egress; the gov APIs are public and keyless.

---

## 6. Production wiring in n8n Cloud

1. Sign up at n8n.io (Pro). 
2. Create credentials (see `n8n-spec/README.md` → "Credentials to create in n8n"):
   - **Microsoft Outlook OAuth2** — same Entra app. n8n shows its **OAuth Redirect URL**; add that URL to the Entra app's redirect URIs (step 1.3). n8n then manages token refresh for you.
   - **Header Auth — Anthropic** (`x-api-key` + `anthropic-version: 2023-06-01`).
   - **Basic Auth — Companies House** (key as username, blank password).
   - n8n **variables**: `TEAMS_WORKFLOW_URL`, `USER_NAME`, `USER_PHONE`, `USER_TIMEZONE`, `MODEL_DRAFTING=claude-sonnet-4-6`, `MODEL_CLASSIFY=claude-haiku-4-5`.
3. Build the five workflows from `n8n-spec/` (node by node — don't paste raw JSON), then **export the JSON yourself** and **activate** them. The Schedule Triggers are what make it 24/7.

---

## Where each value goes — quick map

| Credential | Local (`.env`) | n8n |
|---|---|---|
| Entra client/secret/tenant | `MICROSOFT_*` | Microsoft Outlook OAuth2 credential |
| Redirect URI | `http://localhost` | n8n's OAuth callback URL (add to Entra) |
| Anthropic key | `ANTHROPIC_API_KEY` | Header Auth credential |
| Companies House key | `COMPANIES_HOUSE_API_KEY` | Basic Auth credential |
| Teams URL | `TEAMS_WORKFLOW_URL` | n8n variable |
| Model IDs | `src/config/models.json` | `MODEL_DRAFTING` / `MODEL_CLASSIFY` vars |
