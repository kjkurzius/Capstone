/**
 * teams-notifier.ts
 *
 * Posts notifications into Microsoft Teams via a **Power Automate Workflow**
 * HTTP trigger ("When a Teams webhook request is received").
 *
 * The legacy Office 365 "Incoming Webhook" connector was permanently retired
 * in the May 2026 rollout — DO NOT use it. The supported replacement is the
 * Workflows app trigger, whose generated HTTP URL goes in TEAMS_WORKFLOW_URL.
 *
 * We POST a Teams message containing an Adaptive Card in `attachments`. In the
 * Power Automate flow, map the incoming body to a "Post card in a chat or
 * channel" action. The flow can also read top-level `summary`/`text` fields if
 * you prefer a plain-text post.
 */

import "dotenv/config";

export interface TeamsAlert {
  title: string;
  text: string;
  /** Optional key/value facts rendered as a FactSet. */
  facts?: { name: string; value: string }[];
  urgent?: boolean;
}

function workflowUrl(): string {
  const url = process.env.TEAMS_WORKFLOW_URL ?? "";
  if (!url || url.startsWith("[")) {
    throw new Error("TEAMS_WORKFLOW_URL is missing or a placeholder. Set it in .env.");
  }
  return url;
}

function buildAdaptiveCard(alert: TeamsAlert): unknown {
  const bodyBlocks: unknown[] = [
    {
      type: "TextBlock",
      size: "Medium",
      weight: "Bolder",
      text: alert.title,
      wrap: true,
      color: alert.urgent ? "Attention" : "Default",
    },
    { type: "TextBlock", text: alert.text, wrap: true },
  ];
  if (alert.facts?.length) {
    bodyBlocks.push({
      type: "FactSet",
      facts: alert.facts.map((f) => ({ title: f.name, value: f.value })),
    });
  }
  return {
    type: "message",
    // Top-level mirrors for flows configured to post plain text.
    summary: alert.title,
    text: alert.text,
    attachments: [
      {
        contentType: "application/vnd.microsoft.card.adaptive",
        contentUrl: null,
        content: {
          $schema: "http://adaptivecards.io/schemas/adaptive-card.json",
          type: "AdaptiveCard",
          version: "1.4",
          body: bodyBlocks,
        },
      },
    ],
  };
}

async function post(payload: unknown): Promise<void> {
  const resp = await fetch(workflowUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  // Power Automate returns 202 Accepted on success.
  if (!resp.ok && resp.status !== 202) {
    throw new Error(`Teams workflow POST → ${resp.status}: ${await resp.text()}`);
  }
}

/** Send a single alert (used for urgent items and the re-auth alert). */
export async function sendTeamsAlert(alert: TeamsAlert): Promise<void> {
  await post(buildAdaptiveCard(alert));
}

/**
 * Send the twice-daily digest: routine email items + the Opportunity Score
 * summary, as one card.
 */
export async function sendDigest(opts: {
  heading: string;
  summaryMarkdown: string;
  facts?: { name: string; value: string }[];
}): Promise<void> {
  await sendTeamsAlert({
    title: opts.heading,
    text: opts.summaryMarkdown,
    facts: opts.facts,
    urgent: false,
  });
}
