/**
 * opportunity-score.ts — orchestrator
 *
 *   fetch (live APIs) → compute (deterministic) → compare (vs yesterday)
 *   → report (LLM interprets) → persist snapshot + history → print
 *
 * Run locally: `npm run score`
 *   flags:
 *     --quiet    print ONLY the composite and delta (handy for /loop dev)
 *     --notify   also post the digest to Teams (needs TEAMS_WORKFLOW_URL)
 *
 * In production this same logic is invoked by the n8n Schedule Trigger at
 * 06:00 — see n8n-spec/workflow-opportunity-score.md. n8n is the unattended
 * 24/7 runner; this CLI is for development and verification.
 */

import "dotenv/config";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { loadWeights, computeScore, type ScoringInputs } from "./scoring-model.js";
import { fetchContractsFinder } from "./sources/contracts-finder.js";
import { fetchFindATender, isDefenseSecurity } from "./sources/find-a-tender.js";
import { getCompanyHealth, countRecentFilings } from "./sources/companies-house.js";
import { generateReport, type ReportContext } from "./report-generator.js";
import type { NoticeItem } from "./sources/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = resolve(__dirname, "../../data");
const SCORES_DIR = resolve(DATA_DIR, "scores");
const HISTORY = resolve(DATA_DIR, "score-history.json");

interface HistoryEntry {
  date: string;
  composite: number;
}

function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}

function readHistory(): HistoryEntry[] {
  if (!existsSync(HISTORY)) return [];
  try {
    return JSON.parse(readFileSync(HISTORY, "utf-8")) as HistoryEntry[];
  } catch {
    return [];
  }
}

async function main(): Promise<void> {
  const quiet = process.argv.includes("--quiet");
  const notify = process.argv.includes("--notify");
  const weights = loadWeights();
  const log = (...a: unknown[]) => {
    if (!quiet) console.log(...a);
  };

  log(`\n▶ Opportunity Score for ${weights.target.company}`);
  log(`  keyword query: ${weights.keyword_query}`);

  // --- FETCH (parallel) ---
  const cpvPrefixes =
    weights.categories.find((c) => c.key === "defense_security_notices")?.cpv_prefixes ?? ["35"];

  const [cf, fts, health, filings] = await Promise.all([
    fetchContractsFinder(weights.keyword_query),
    fetchFindATender(weights.keyword_query),
    getCompanyHealth(weights.target.companies_house_number),
    countRecentFilings(weights.competitor_company_numbers),
  ]);

  const notices: NoticeItem[] = [...cf.items, ...fts.items];
  const dataQualityNotes: string[] = [];
  for (const r of [cf, fts]) if (!r.ok) dataQualityNotes.push(r.note);
  if (!health.ok) dataQualityNotes.push(health.note);
  if (!filings.ok) dataQualityNotes.push(filings.note);

  // --- DERIVE COUNTABLE INPUTS ---
  const defenseCount = notices.filter((n) => isDefenseSecurity(n.cpv, cpvPrefixes)).length;
  const totalValue = notices.reduce((s, n) => s + (n.valueGbp ?? 0), 0);

  const inputs: ScoringInputs = {
    new_relevant_tenders: notices.length,
    defense_security_notices: defenseCount,
    competitor_filings: filings.count,
    tender_value_signal: totalValue,
    company_health: {
      ok: health.ok,
      status: health.status,
      hasInsolvencyHistory: health.hasInsolvencyHistory,
      hasOverdueAccounts: health.hasOverdueAccounts,
      hasOverdueConfirmation: health.hasOverdueConfirmation,
    },
  };

  // --- COMPUTE (deterministic) ---
  const score = computeScore(inputs, weights);

  // --- COMPARE ---
  const history = readHistory();
  const prev = history.length ? history[history.length - 1] : null;
  const previousComposite = prev && prev.date !== todayStr() ? prev.composite : prev?.composite ?? null;
  const delta =
    previousComposite === null ? null : Math.round((score.composite - previousComposite) * 10) / 10;

  // --- REPORT (LLM interprets, never invents the number) ---
  const ctx: ReportContext = {
    company: weights.target.company,
    score,
    previousComposite,
    delta,
    alertThreshold: weights.alert_threshold_delta,
    notices,
    dataQualityNotes,
  };
  const report = await generateReport(ctx);

  // --- PERSIST ---
  mkdirSync(SCORES_DIR, { recursive: true });
  const snapshot = {
    date: todayStr(),
    company: weights.target.company,
    composite: score.composite,
    delta,
    material: report.material,
    inputs,
    categories: score.categories,
    notices,
    dataQualityNotes,
    summaryGeneratedBy: report.generatedBy,
    summary: report.prose,
    computedAt: score.computedAt,
  };
  writeFileSync(resolve(SCORES_DIR, `${todayStr()}.json`), JSON.stringify(snapshot, null, 2));

  // Append/replace today's history entry.
  const filtered = history.filter((h) => h.date !== todayStr());
  filtered.push({ date: todayStr(), composite: score.composite });
  writeFileSync(HISTORY, JSON.stringify(filtered, null, 2));

  // --- NOTIFY (optional) ---
  if (notify) {
    try {
      const { sendDigest } = await import("../utils/teams-notifier.js");
      await sendDigest({
        heading: `${weights.target.company} — Opportunity Score ${score.composite}/100${
          delta !== null ? ` (${delta >= 0 ? "+" : ""}${delta})` : ""
        }`,
        summaryMarkdown: report.prose,
        facts: score.categories.map((c) => ({ name: c.key, value: String(c.points) })),
      });
      log("  ✅ Posted digest to Teams.");
    } catch (e) {
      log(`  ⚠️ Teams notify failed: ${(e as Error).message}`);
    }
  }

  // --- OUTPUT ---
  if (quiet) {
    console.log(`${score.composite} ${delta === null ? "n/a" : (delta >= 0 ? "+" : "") + delta}`);
    return;
  }
  log("\n— Category points —");
  for (const c of score.categories) log(`  ${c.key.padEnd(26)} ${String(c.points).padStart(6)}`);
  log(`\n  COMPOSITE: ${score.composite}/100`);
  log(`  DELTA:     ${delta === null ? "n/a (first run)" : (delta >= 0 ? "+" : "") + delta}`);
  log(`  MATERIAL:  ${report.material ? "YES — flagged" : "no"}`);
  log(`  SUMMARY (${report.generatedBy}):\n`);
  log(report.prose);
  log(`\n  Snapshot: data/scores/${todayStr()}.json`);
}

main().catch((e) => {
  console.error("Opportunity score run failed:", e);
  process.exit(1);
});
