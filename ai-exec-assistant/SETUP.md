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
2. Create the four credentials below, with these exact field values, then the variables.

### Credential A — Microsoft (Graph: mail + calendar)

Credentials → **New** → search **"Microsoft OAuth2 API"** (generic — works for every Graph HTTP Request node *and* the built-in Microsoft Outlook node; use this one so calendar calls aren't blocked by a narrower scope set).

| Field | Value |
|---|---|
| Grant Type | Authorization Code |
| Authorization URL | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/authorize` |
| Access Token URL | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token` |
| Client ID | your Entra **Application (client) ID** |
| Client Secret | your Entra client secret **Value** |
| Scope | `offline_access User.Read Mail.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite` |
| Auth | Header / Body (leave n8n default) |

n8n shows an **OAuth Redirect URL** like `https://<your>.app.n8n.cloud/rest/oauth2-credential/callback`. Copy it into the Entra app → **Authentication** → **Add a redirect URI** (Web). Then click **Connect / Sign in** in n8n and approve. n8n stores + auto-refreshes the token from here on.

### Credential B — Anthropic (Claude HTTP nodes)

Credentials → **New** → **Header Auth**.

| Field | Value |
|---|---|
| Name | `x-api-key` |
| Value | your `ANTHROPIC_API_KEY` |

(`anthropic-version: 2023-06-01` is added as a static header on each Claude HTTP Request node — Header Auth only carries the key.)

### Credential C — Companies House

Credentials → **New** → **Basic Auth**.

| Field | Value |
|---|---|
| User | your Companies House API key |
| Password | *(leave blank)* |

### Credential D — Teams (Power Automate Workflow)

No credential object needed — the URL is the secret. Store it as a variable (below) and POST to it from an HTTP Request node.

### Variables (Settings → Variables, or instance env)

```
TEAMS_WORKFLOW_URL = <Power Automate HTTP URL from step 4>
USER_NAME          = <full name>
USER_PHONE         = <phone>
USER_TIMEZONE      = Europe/London
MODEL_DRAFTING     = claude-sonnet-4-6
MODEL_CLASSIFY     = claude-haiku-4-5
```

3. Build the five workflows from `n8n-spec/` (node by node — don't paste raw JSON), wiring each node to the credential above that matches its `n8n-spec` entry. Then **export the JSON yourself** and **activate** them. The Schedule Triggers are what make it 24/7.

### Connection smoke tests inside n8n (before activating)

- **Microsoft:** a one-node HTTP Request GET `https://graph.microsoft.com/v1.0/me` with Credential A → should return your profile.
- **Anthropic:** HTTP Request POST `https://api.anthropic.com/v1/messages` with Credential B + header `anthropic-version: 2023-06-01`, body `{"model":"{{$vars.MODEL_CLASSIFY}}","max_tokens":16,"messages":[{"role":"user","content":"ping"}]}` → 200.
- **Companies House:** HTTP Request GET `https://api.company-information.service.gov.uk/company/08631843` with Credential C → company JSON.
- **Teams:** HTTP Request POST `{{$vars.TEAMS_WORKFLOW_URL}}` with body `{"text":"n8n connection test"}` → 202 and a message in the channel.

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
