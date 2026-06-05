/**
 * scoring-model.ts
 *
 * DETERMINISTIC scoring. No LLM, no randomness — the same inputs always
 * produce the same number. The composite is a weighted sum (0–100) of
 * normalized countable metrics. Weights and normalization come from
 * src/config/scoring-weights.json.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

type Normalize =
  | { type: "linear"; zero_at: number; full_at: number }
  | { type: "inverse_linear"; full_at: number; zero_at: number }
  | { type: "log"; zero_at: number; full_at: number }
  | { type: "health_flags" };

interface Category {
  key: string;
  weight: number;
  source: string;
  metric: string;
  normalize: Normalize;
  cpv_prefixes?: string[];
}

export interface ScoringWeights {
  version: number;
  scale: number;
  keyword_query: string;
  competitor_company_numbers: string[];
  target: { company: string; domain: string; companies_house_number: string };
  categories: Category[];
  alert_threshold_delta: number;
}

export function loadWeights(): ScoringWeights {
  return JSON.parse(
    readFileSync(resolve(__dirname, "../config/scoring-weights.json"), "utf-8"),
  ) as ScoringWeights;
}

export interface HealthInput {
  ok: boolean;
  status?: string;
  hasInsolvencyHistory?: boolean;
  hasOverdueAccounts?: boolean;
  hasOverdueConfirmation?: boolean;
}

export interface ScoringInputs {
  new_relevant_tenders: number;
  defense_security_notices: number;
  competitor_filings: number;
  tender_value_signal: number;
  company_health: HealthInput;
}

export interface CategoryScore {
  key: string;
  weight: number;
  rawMetric: number | string;
  normalized: number; // 0..1
  points: number; // normalized * weight * scale
}

export interface ScoreResult {
  composite: number; // 0..100, rounded to 1 decimal
  categories: CategoryScore[];
  computedAt: string;
}

function clamp01(x: number): number {
  return Math.max(0, Math.min(1, x));
}

function normalizeMetric(n: Normalize, value: number, health: HealthInput): number {
  switch (n.type) {
    case "linear":
      return clamp01((value - n.zero_at) / (n.full_at - n.zero_at || 1));
    case "inverse_linear":
      // More activity (toward zero_at) => lower score; full_at value => 1.0.
      return clamp01((n.zero_at - value) / (n.zero_at - n.full_at || 1));
    case "log": {
      const denom = Math.log10(1 + n.full_at) || 1;
      return clamp01(Math.log10(1 + Math.max(0, value)) / denom);
    }
    case "health_flags": {
      // Start full; status not active is decisive; each adverse flag costs.
      if (!health.ok) return 0.5; // unknown → neutral, flagged in report
      let s = 1.0;
      if ((health.status ?? "").toLowerCase() !== "active") s -= 0.6;
      if (health.hasInsolvencyHistory) s -= 0.25;
      if (health.hasOverdueAccounts) s -= 0.1;
      if (health.hasOverdueConfirmation) s -= 0.05;
      return clamp01(s);
    }
  }
}

export function computeScore(inputs: ScoringInputs, weights: ScoringWeights): ScoreResult {
  const scale = weights.scale;
  const categories: CategoryScore[] = weights.categories.map((cat) => {
    let raw: number;
    switch (cat.key) {
      case "new_relevant_tenders":
        raw = inputs.new_relevant_tenders;
        break;
      case "defense_security_notices":
        raw = inputs.defense_security_notices;
        break;
      case "competitor_filings":
        raw = inputs.competitor_filings;
        break;
      case "tender_value_signal":
        raw = inputs.tender_value_signal;
        break;
      case "company_health":
        raw = 0; // metric is the health object, normalized below
        break;
      default:
        raw = 0;
    }
    const normalized = normalizeMetric(cat.normalize, raw, inputs.company_health);
    const points = normalized * cat.weight * scale;
    const rawMetric: number | string =
      cat.key === "company_health"
        ? `${inputs.company_health.status ?? "unknown"}${
            inputs.company_health.hasInsolvencyHistory ? " +insolvency-history" : ""
          }`
        : raw;
    return {
      key: cat.key,
      weight: cat.weight,
      rawMetric,
      normalized: Math.round(normalized * 1000) / 1000,
      points: Math.round(points * 100) / 100,
    };
  });

  const composite = categories.reduce((sum, c) => sum + c.points, 0);
  return {
    composite: Math.round(composite * 10) / 10,
    categories,
    computedAt: new Date().toISOString(),
  };
}
