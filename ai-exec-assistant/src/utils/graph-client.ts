/**
 * graph-client.ts
 *
 * Microsoft Graph v1.0 client using the OAuth 2.0 delegated (authorization
 * code) flow with automatic access-token refresh.
 *
 * KNOWN LIMITATION (documented, not glossed over):
 *   Delegated refresh tokens are NOT permanent. They can be invalidated by:
 *     - refresh-token expiry (default ~90 days of inactivity),
 *     - a Conditional Access policy / password change / admin revocation,
 *     - MFA re-challenge requirements.
 *   When the refresh grant fails, this client throws `ReauthRequiredError`
 *   and fires a Teams alert (sendReauthAlert) so a human can re-run the
 *   interactive `npm run auth` flow. There is no way to fully automate
 *   delegated re-auth — interactive sign-in is required by design.
 *
 * For PRODUCTION the n8n Microsoft 365 / Outlook node manages its own
 * delegated credential and token refresh. This file is the local-dev
 * bootstrap + the reference implementation of the request bodies that the
 * n8n HTTP Request nodes replicate.
 *
 * CLI:
 *   tsx src/utils/graph-client.ts --auth      interactive sign-in, caches refresh token
 *   tsx src/utils/graph-client.ts --whoami     GET /me, prints displayName + mail
 */

import "dotenv/config";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { createInterface } from "node:readline/promises";

const GRAPH_BASE = "https://graph.microsoft.com/v1.0";

const SCOPES = [
  "offline_access",
  "User.Read",
  "Mail.Read",
  "Mail.ReadWrite",
  "Mail.Send",
  "Calendars.ReadWrite",
].join(" ");

export class ReauthRequiredError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReauthRequiredError";
  }
}

interface TokenCache {
  access_token: string;
  refresh_token: string;
  expires_at: number; // epoch ms
}

interface GraphEnv {
  clientId: string;
  clientSecret: string;
  tenantId: string;
  redirectUri: string;
  cachePath: string;
}

function loadEnv(): GraphEnv {
  const clientId = process.env.MICROSOFT_CLIENT_ID ?? "";
  const clientSecret = process.env.MICROSOFT_CLIENT_SECRET ?? "";
  const tenantId = process.env.MICROSOFT_TENANT_ID ?? "";
  const redirectUri = process.env.MICROSOFT_REDIRECT_URI ?? "";
  const cachePath = process.env.GRAPH_TOKEN_CACHE ?? ".graph-token-cache.json";
  for (const [k, v] of Object.entries({ clientId, clientSecret, tenantId, redirectUri })) {
    if (!v || v.startsWith("[")) {
      throw new Error(`Microsoft Graph env var for "${k}" is missing or a placeholder. Fill in .env.`);
    }
  }
  return { clientId, clientSecret, tenantId, redirectUri, cachePath };
}

function tokenUrl(tenantId: string): string {
  return `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`;
}
function authorizeUrl(env: GraphEnv): string {
  const p = new URLSearchParams({
    client_id: env.clientId,
    response_type: "code",
    redirect_uri: env.redirectUri,
    response_mode: "query",
    scope: SCOPES,
    prompt: "select_account",
  });
  return `https://login.microsoftonline.com/${env.tenantId}/oauth2/v2.0/authorize?${p.toString()}`;
}

function readCache(path: string): TokenCache | null {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8")) as TokenCache;
  } catch {
    return null;
  }
}
function writeCache(path: string, cache: TokenCache): void {
  writeFileSync(path, JSON.stringify(cache, null, 2));
}

export class GraphClient {
  private env: GraphEnv;
  private cache: TokenCache | null;

  constructor() {
    this.env = loadEnv();
    this.cache = readCache(this.env.cachePath);
  }

  /** Interactive authorization-code sign-in. Persists the refresh token. */
  async interactiveAuth(): Promise<void> {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    console.log("\nOpen this URL in a browser, sign in, and approve:\n");
    console.log(authorizeUrl(this.env));
    console.log(
      `\nAfter approving you'll be redirected to ${this.env.redirectUri}?code=...`,
    );
    const pasted = (
      await rl.question("\nPaste the full redirect URL (or just the code): ")
    ).trim();
    rl.close();

    let code = pasted;
    if (pasted.includes("code=")) {
      try {
        code = new URL(pasted).searchParams.get("code") ?? pasted;
      } catch {
        const m = pasted.match(/[?&]code=([^&]+)/);
        if (m) code = decodeURIComponent(m[1]);
      }
    }

    const body = new URLSearchParams({
      client_id: this.env.clientId,
      client_secret: this.env.clientSecret,
      grant_type: "authorization_code",
      code,
      redirect_uri: this.env.redirectUri,
      scope: SCOPES,
    });
    const cache = await this.exchange(body);
    this.cache = cache;
    writeCache(this.env.cachePath, cache);
    console.log(`\n✅ Authenticated. Token cached at ${this.env.cachePath}.`);
  }

  /** Returns a valid access token, refreshing if necessary. */
  async getAccessToken(): Promise<string> {
    if (!this.cache) {
      throw new ReauthRequiredError(
        "No cached token. Run `npm run auth` to sign in interactively.",
      );
    }
    // 60s safety margin.
    if (Date.now() < this.cache.expires_at - 60_000) {
      return this.cache.access_token;
    }
    // Refresh.
    const body = new URLSearchParams({
      client_id: this.env.clientId,
      client_secret: this.env.clientSecret,
      grant_type: "refresh_token",
      refresh_token: this.cache.refresh_token,
      scope: SCOPES,
    });
    try {
      const refreshed = await this.exchange(body);
      this.cache = refreshed;
      writeCache(this.env.cachePath, refreshed);
      return refreshed.access_token;
    } catch (err) {
      const msg =
        "Microsoft Graph refresh token is no longer valid (expiry / conditional access / revocation). " +
        "Interactive re-auth is required: run `npm run auth`.";
      await sendReauthAlert(msg).catch(() => {});
      throw new ReauthRequiredError(`${msg} Underlying error: ${(err as Error).message}`);
    }
  }

  private async exchange(body: URLSearchParams): Promise<TokenCache> {
    const resp = await fetch(tokenUrl(this.env.tenantId), {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    const json = (await resp.json()) as Record<string, unknown>;
    if (!resp.ok) {
      throw new Error(
        `Token endpoint ${resp.status}: ${json.error ?? ""} ${json.error_description ?? ""}`,
      );
    }
    const expiresInSec = Number(json.expires_in ?? 3600);
    return {
      access_token: String(json.access_token),
      // Refresh token rotates; fall back to the previous one if not returned.
      refresh_token: String(json.refresh_token ?? this.cache?.refresh_token ?? ""),
      expires_at: Date.now() + expiresInSec * 1000,
    };
  }

  // ---- Graph REST helpers ----

  async graphGet<T = unknown>(path: string): Promise<T> {
    const token = await this.getAccessToken();
    const resp = await fetch(`${GRAPH_BASE}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) {
      throw new Error(`Graph GET ${path} → ${resp.status}: ${await resp.text()}`);
    }
    return (await resp.json()) as T;
  }

  async graphPost<T = unknown>(path: string, payload: unknown): Promise<T> {
    const token = await this.getAccessToken();
    const resp = await fetch(`${GRAPH_BASE}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      throw new Error(`Graph POST ${path} → ${resp.status}: ${await resp.text()}`);
    }
    // sendMail returns 202 with empty body.
    const text = await resp.text();
    return (text ? JSON.parse(text) : {}) as T;
  }

  whoami(): Promise<{ displayName: string; mail: string; userPrincipalName: string }> {
    return this.graphGet("/me?$select=displayName,mail,userPrincipalName");
  }

  /** Messages received before `olderThanIso` (used for the >4h unanswered rule). */
  listRecentMessages(olderThanIso: string, top = 25): Promise<{ value: GraphMessage[] }> {
    const filter = encodeURIComponent(`receivedDateTime le ${olderThanIso}`);
    const select = "id,subject,from,receivedDateTime,bodyPreview,isRead,conversationId";
    return this.graphGet(
      `/me/messages?$filter=${filter}&$orderby=receivedDateTime desc&$top=${top}&$select=${select}`,
    );
  }

  /** Create a DRAFT reply (never auto-send). Returns the draft message. */
  createReplyDraft(messageId: string): Promise<GraphMessage> {
    return this.graphPost(`/me/messages/${messageId}/createReply`, {});
  }

  /** Update a draft's body (e.g. inject the AI-drafted reply + signature). */
  updateMessage(messageId: string, body: { contentType: "Text" | "HTML"; content: string }) {
    // PATCH via fetch (helper kept inline because it's the only PATCH we use).
    return this.patch(`/me/messages/${messageId}`, { body });
  }

  private async patch<T = unknown>(path: string, payload: unknown): Promise<T> {
    const token = await this.getAccessToken();
    const resp = await fetch(`${GRAPH_BASE}${path}`, {
      method: "PATCH",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`Graph PATCH ${path} → ${resp.status}: ${await resp.text()}`);
    const text = await resp.text();
    return (text ? JSON.parse(text) : {}) as T;
  }

  listCalendarEvents(startIso: string, endIso: string): Promise<{ value: GraphEvent[] }> {
    const params = new URLSearchParams({
      startDateTime: startIso,
      endDateTime: endIso,
      $select: "subject,start,end",
      $orderby: "start/dateTime",
    });
    return this.graphGet(`/me/calendarView?${params.toString()}`);
  }

  /** Create a calendar event — call ONLY after human approval. */
  createEvent(event: GraphEventCreate): Promise<GraphEvent> {
    return this.graphPost("/me/events", event);
  }
}

export interface GraphMessage {
  id: string;
  subject: string;
  from?: { emailAddress: { name: string; address: string } };
  receivedDateTime: string;
  bodyPreview: string;
  isRead: boolean;
  conversationId: string;
}
export interface GraphEvent {
  subject: string;
  start: { dateTime: string; timeZone: string };
  end: { dateTime: string; timeZone: string };
}
export interface GraphEventCreate {
  subject: string;
  body?: { contentType: "HTML" | "Text"; content: string };
  start: { dateTime: string; timeZone: string };
  end: { dateTime: string; timeZone: string };
  attendees?: { emailAddress: { address: string; name?: string }; type: "required" | "optional" }[];
}

/** Fire a Teams alert that interactive re-auth is needed. Best-effort. */
async function sendReauthAlert(message: string): Promise<void> {
  const { sendTeamsAlert } = await import("./teams-notifier.js");
  await sendTeamsAlert({
    title: "⚠️ Microsoft Graph re-authentication required",
    text: message,
    urgent: true,
  });
}

// ---- CLI ----
const isMain = (() => {
  try {
    return process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
  } catch {
    return false;
  }
})();

if (isMain || process.argv.some((a) => a === "--auth" || a === "--whoami")) {
  const arg = process.argv[2] ?? process.argv.find((a) => a.startsWith("--"));
  const gc = new GraphClient();
  if (arg === "--auth") {
    gc.interactiveAuth().catch((e) => {
      console.error(e.message);
      process.exit(1);
    });
  } else if (arg === "--whoami") {
    gc.whoami()
      .then((me) => console.log(`Signed in as ${me.displayName} <${me.mail ?? me.userPrincipalName}>`))
      .catch((e) => {
        console.error(e.message);
        process.exit(1);
      });
  }
}
