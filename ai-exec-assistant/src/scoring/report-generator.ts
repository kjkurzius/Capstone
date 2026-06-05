/**
 * report-generator.ts
 *
 * Turns the DETERMINISTIC numbers from scoring-model into a readable report.
 * The LLM (Sonnet via opportunity-summary.txt) interprets the numbers and
 * writes prose — top 3 opportunities, top 3 risks, recommended actions. It is
 * explicitly told the score is already computed and must NOT be changed.
 *
 * If ANTHROPIC_API_KEY is absent or the call fails, a deterministic template
 * summary is produced so the scoring pipeline still completes.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { draft } from "../utils/claude-client.js";
import type { ScoreResult } from "./scoring-model.js";
import type { NoticeItem } from "./sources/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

const PROMPT = readFileSync(
  resolve(__dirname, "../prompts/opportunity-summary.txt"),
  "utf-8",
);

export interface ReportContext {
  company: string;
  score: ScoreResult;
  previousComposite: number | null;
  delta: number | null;
  alertThreshold: number;
  notices: NoticeItem[];
  dataQualityNotes: string[];
}

export interface OpportunityReport {
  composite: number;
  delta: number | null;
  material: boolean;
  prose: string;
  generatedBy: "llm" | "fallback";
}

function buildFactsBlock(ctx: ReportContext): string {
  const lines = ctx.score.categories.map(
    (c) =>
      `- ${c.key}: raw=${c.rawMetric}, normalized=${c.normalized}, points=${c.points} (weight ${c.weight})`,
  );
  const topNotices = ctx.notices
    .slice(0, 8)
    .map(
      (n) =>
        `  • [${n.source}] ${n.title}${n.valueGbp ? ` (~£${Math.round(n.valueGbp).toLocaleString()})` : ""} — ${n.published}`,
    );
  return [
    `Company: ${ctx.company}`,
    `Composite score (DETERMINISTIC, already computed — DO NOT change): ${ctx.score.composite}/100`,
    ctx.previousComposite === null
      ? "Previous score: none (first run)"
      : `Previous score: ${ctx.previousComposite}/100`,
    ctx.delta === null ? "Delta: n/a" : `Delta vs yesterday: ${ctx.delta >= 0 ? "+" : ""}${ctx.delta}`,
    `Material-change alert threshold: ±${ctx.alertThreshold}`,
    "",
    "Category breakdown:",
    ...lines,
    "",
    `Recent matching notices (${ctx.notices.length} total):`,
    ...(topNotices.length ? topNotices : ["  (none in window)"]),
    ...(ctx.dataQualityNotes.length
      ? ["", "Data-quality notes:", ...ctx.dataQualityNotes.map((n) => `  ! ${n}`)]
      : []),
  ].join("\n");
}

function fallbackProse(ctx: ReportContext): string {
  const deltaText =
    ctx.delta === null
      ? "First run — no day-over-day comparison yet."
      : `Day-over-day change: ${ctx.delta >= 0 ? "+" : ""}${ctx.delta}.`;
  const top = ctx.notices
    .slice(0, 3)
    .map((n) => `- ${n.title} [${n.source}]`)
    .join("\n");
  return [
    `**Opportunity Score: ${ctx.score.composite}/100** for ${ctx.company}. ${deltaText}`,
    "",
    top ? `Top recent notices:\n${top}` : "No matching notices in the last 7 days.",
    ctx.dataQualityNotes.length
      ? `\n_Data-quality notes: ${ctx.dataQualityNotes.join("; ")}_`
      : "",
    "\n_(LLM summary unavailable — deterministic template used.)_",
  ].join("\n");
}

export async function generateReport(ctx: ReportContext): Promise<OpportunityReport> {
  const material = ctx.delta !== null && Math.abs(ctx.delta) >= ctx.alertThreshold;
  const facts = buildFactsBlock(ctx);

  let prose: string;
  let generatedBy: "llm" | "fallback";
  try {
    prose = await draft(facts, PROMPT);
    if (!prose) throw new Error("empty LLM response");
    generatedBy = "llm";
  } catch {
    prose = fallbackProse(ctx);
    generatedBy = "fallback";
  }

  return { composite: ctx.score.composite, delta: ctx.delta, material, prose, generatedBy };
}
