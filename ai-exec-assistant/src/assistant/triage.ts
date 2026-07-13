/**
 * triage.ts — local runner for Subsystem A (email → classify → draft →
 * propose-slots / booking-link), the draft-then-approve loop.
 *
 * This is the local-dev / verification entry point for the email subsystem;
 * in production the same steps run as the n8n `workflow-email-monitor`
 * (+ calendar / restaurant sub-workflows). It NEVER sends mail and NEVER
 * creates a calendar event — it drafts and prints for approval. With
 * --create-drafts it creates Outlook DRAFTS (still unsent) via Graph.
 *
 * Run:
 *   npm run triage                 dry run: classify + draft + propose, print only
 *   npm run triage -- --create-drafts   also create Outlook drafts (never sends)
 *   npm run triage -- --notify          post urgent items to Teams
 *   npm run triage -- --max 5           cap messages processed
 *
 * Requires: Microsoft Graph auth (`npm run auth`) and ANTHROPIC_API_KEY.
 */

import "dotenv/config";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { GraphClient, type GraphMessage } from "../utils/graph-client.js";
import { classify, draft } from "../utils/claude-client.js";
import {
  loadCalendarRules,
  proposeSlots,
  slotToGraphEvent,
  type BusyInterval,
} from "../utils/calendar-planner.js";
import { buildOpenTableSearchLink } from "../utils/opentable-linker.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const prompt = (name: string) => readFileSync(resolve(__dirname, `../prompts/${name}`), "utf-8");
const URGENCY_PROMPT = prompt("urgency-classifier.txt");
const TRIAGE_PROMPT = prompt("email-triage.txt");
const CALENDAR_PROMPT = prompt("calendar-extract.txt");

interface Args {
  createDrafts: boolean;
  notify: boolean;
  max: number;
}
function parseArgs(): Args {
  const a = process.argv.slice(2);
  const maxIdx = a.indexOf("--max");
  return {
    createDrafts: a.includes("--create-drafts"),
    notify: a.includes("--notify"),
    max: maxIdx >= 0 ? Number(a[maxIdx + 1]) : 25,
  };
}

function applySignature(reply: string): string {
  return reply
    .replaceAll("[USER_NAME]", process.env.USER_NAME ?? "the executive")
    .replaceAll("[USER_PHONE]", process.env.USER_PHONE ?? "the office number");
}

/** Split the email-triage.txt "NOTES:/REPLY:" response. */
function parseTriage(text: string): { notes: string; reply: string } {
  const replyIdx = text.indexOf("REPLY:");
  if (replyIdx === -1) return { notes: "", reply: text.trim() };
  const notes = text.slice(0, replyIdx).replace(/^NOTES:\s*/i, "").trim();
  const reply = text.slice(replyIdx + "REPLY:".length).trim();
  return { notes, reply };
}

function detectIntent(notes: string): "scheduling" | "restaurant" | "reply" {
  const n = notes.toLowerCase();
  if (/(schedul|meeting|call|calendar|book time|availab)/.test(n)) return "scheduling";
  if (/(restaurant|reservation|book a table|dinner|lunch reservation|opentable)/.test(n)) return "restaurant";
  return "reply";
}

/** Convert Graph calendar events to busy intervals (Graph default TZ is UTC). */
function toBusy(events: { start: { dateTime: string }; end: { dateTime: string } }[]): BusyInterval[] {
  const asDate = (s: string) =>
    new Date(/[Z+]|[+-]\d\d:\d\d$/.test(s) ? s : `${s}Z`);
  return events.map((e) => ({ start: asDate(e.start.dateTime), end: asDate(e.end.dateTime) }));
}

async function handleScheduling(gc: GraphClient, body: string): Promise<string> {
  const rules = loadCalendarRules();
  let intent: { duration_minutes?: number; earliest_date?: string; latest_date?: string; meeting_purpose?: string };
  try {
    intent = JSON.parse(await draft(body, CALENDAR_PROMPT));
  } catch {
    return "  (scheduling intent could not be parsed — falling back to a normal reply)";
  }
  const now = new Date();
  const end = new Date(now.getTime() + (rules.lookahead_business_days + 3) * 86_400_000);
  let busy: BusyInterval[] = [];
  try {
    const cal = await gc.listCalendarEvents(now.toISOString(), end.toISOString());
    busy = toBusy(cal.value);
  } catch (e) {
    return `  (could not read calendar: ${(e as Error).message})`;
  }
  const slots = proposeSlots(rules, {
    now,
    busy,
    durationMinutes: intent.duration_minutes,
    earliest: intent.earliest_date ? new Date(intent.earliest_date) : undefined,
    latest: intent.latest_date ? new Date(intent.latest_date) : undefined,
  });
  if (!slots.length) return "  (no free slots found in the lookahead window)";
  const lines = slots.map((s) => {
    const ev = slotToGraphEvent(s, rules.timezone);
    return `    • ${ev.start.dateTime} – ${ev.end.dateTime} (${rules.timezone})`;
  });
  return `  Proposed slots (create event only after approval):\n${lines.join("\n")}`;
}

function handleRestaurant(body: string): string {
  // We never invent an OpenTable rid; without one we offer a search link.
  const link = buildOpenTableSearchLink(body.slice(0, 60));
  return `  Restaurant request detected. Pre-filled booking needs the venue's OpenTable rid;\n  offering a search link for the human to complete: ${link}`;
}

async function processMessage(gc: GraphClient, args: Args, msg: GraphMessage): Promise<void> {
  const sender = msg.from?.emailAddress;
  const header = `\n▶ ${msg.subject || "(no subject)"} — from ${sender?.name ?? "?"} <${sender?.address ?? "?"}> @ ${msg.receivedDateTime}`;
  const userContent = `Subject: ${msg.subject}\nFrom: ${sender?.address ?? ""}\n\n${msg.bodyPreview}`;

  const urgency = (await classify(userContent, URGENCY_PROMPT)).toLowerCase().trim();
  console.log(header);
  console.log(`  urgency: ${urgency}`);
  if (urgency === "ignore") {
    console.log("  → ignored (no action)");
    return;
  }

  const { notes, reply } = parseTriage(await draft(userContent, TRIAGE_PROMPT));
  const intent = detectIntent(notes);
  console.log(`  intent: ${intent}${notes ? ` — ${notes}` : ""}`);

  if (intent === "scheduling") console.log(await handleScheduling(gc, userContent));
  if (intent === "restaurant") console.log(handleRestaurant(userContent));

  const finalReply = applySignature(reply);
  console.log("  drafted reply (NOT sent):\n" + finalReply.split("\n").map((l) => `    ${l}`).join("\n"));

  if (args.createDrafts) {
    try {
      const draftMsg = await gc.createReplyDraft(msg.id);
      await gc.updateMessage(draftMsg.id, { contentType: "Text", content: finalReply });
      console.log(`  ✅ Outlook draft created (id ${draftMsg.id}) — awaiting your review/send.`);
    } catch (e) {
      console.log(`  ⚠️ could not create draft: ${(e as Error).message}`);
    }
  }

  if (urgency === "urgent" && args.notify) {
    try {
      const { sendTeamsAlert } = await import("../utils/teams-notifier.js");
      await sendTeamsAlert({
        title: `⚡ Urgent email: ${msg.subject}`,
        text: `From ${sender?.name ?? sender?.address}. A draft reply is ready for your approval.`,
        urgent: true,
      });
      console.log("  📣 urgent alert posted to Teams.");
    } catch (e) {
      console.log(`  ⚠️ Teams alert failed: ${(e as Error).message}`);
    }
  }
}

async function main(): Promise<void> {
  const args = parseArgs();
  const gc = new GraphClient();
  const cutoff = new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString();
  console.log(`Fetching unread messages received before ${cutoff} (>4h old)…`);

  const { value: messages } = await gc.listRecentMessages(cutoff, args.max);
  const unanswered = messages.filter((m) => !m.isRead);
  console.log(`${unanswered.length} candidate message(s). Mode: ${args.createDrafts ? "create-drafts" : "dry-run"}.`);

  for (const msg of unanswered) {
    try {
      await processMessage(gc, args, msg);
    } catch (e) {
      console.log(`  ⚠️ failed on "${msg.subject}": ${(e as Error).message}`);
    }
  }
  console.log("\nDone. No mail was sent and no events were created — review drafts and approve to send.");
}

main().catch((e) => {
  console.error("Triage run failed:", e.message ?? e);
  process.exit(1);
});
