/**
 * Proves the Opportunity Score is deterministic and that each normalization
 * type behaves as configured. Uses the REAL scoring-weights.json via
 * loadWeights() so the test guards the shipped config too.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { computeScore, loadWeights, type ScoringInputs } from "../src/scoring/scoring-model.js";

const weights = loadWeights();

function inputs(partial: Partial<ScoringInputs>): ScoringInputs {
  return {
    new_relevant_tenders: 0,
    defense_security_notices: 0,
    competitor_filings: 0,
    tender_value_signal: 0,
    company_health: { ok: true, status: "active" },
    ...partial,
  };
}

test("weights sum to 1.0", () => {
  const sum = weights.categories.reduce((s, c) => s + c.weight, 0);
  assert.ok(Math.abs(sum - 1.0) < 1e-9, `weights sum to ${sum}, expected 1.0`);
});

test("all metrics maxed → composite 100", () => {
  const r = computeScore(
    inputs({
      new_relevant_tenders: 10,
      defense_security_notices: 5,
      competitor_filings: 0, // inverse: zero competitor activity is best
      tender_value_signal: 5_000_000,
      company_health: { ok: true, status: "active" },
    }),
    weights,
  );
  assert.equal(r.composite, 100);
});

test("deterministic: same inputs → identical result", () => {
  const i = inputs({ new_relevant_tenders: 4, tender_value_signal: 250_000 });
  const a = computeScore(i, weights);
  const b = computeScore(i, weights);
  assert.equal(a.composite, b.composite);
  assert.deepEqual(
    a.categories.map((c) => c.points),
    b.categories.map((c) => c.points),
  );
});

test("linear normalization: half of full_at → half the points", () => {
  const r = computeScore(inputs({ new_relevant_tenders: 5 }), weights); // full_at=10
  const cat = r.categories.find((c) => c.key === "new_relevant_tenders")!;
  assert.equal(cat.normalized, 0.5);
  assert.equal(cat.points, 15); // 0.5 * 0.30 * 100
});

test("inverse_linear: more competitor filings → fewer points", () => {
  const none = computeScore(inputs({ competitor_filings: 0 }), weights);
  const some = computeScore(inputs({ competitor_filings: 15 }), weights);
  const p0 = none.categories.find((c) => c.key === "competitor_filings")!.points;
  const p15 = some.categories.find((c) => c.key === "competitor_filings")!.points;
  assert.equal(p0, 15); // full
  assert.equal(p15, 0); // zero_at
});

test("log normalization is concave (caps at full_at)", () => {
  const cap = computeScore(inputs({ tender_value_signal: 5_000_000 }), weights);
  const over = computeScore(inputs({ tender_value_signal: 50_000_000 }), weights);
  const capPts = cap.categories.find((c) => c.key === "tender_value_signal")!.points;
  const overPts = over.categories.find((c) => c.key === "tender_value_signal")!.points;
  assert.equal(capPts, 20); // full
  assert.equal(overPts, 20); // clamped, never exceeds full
});

test("health_flags: adverse flags reduce the health points", () => {
  const healthy = computeScore(inputs({ company_health: { ok: true, status: "active" } }), weights);
  const liquidation = computeScore(
    inputs({ company_health: { ok: true, status: "liquidation" } }),
    weights,
  );
  const unknown = computeScore(inputs({ company_health: { ok: false } }), weights);
  const h = (r: ReturnType<typeof computeScore>) =>
    r.categories.find((c) => c.key === "company_health")!.points;
  assert.equal(h(healthy), 15); // 1.0 * 0.15 * 100
  assert.ok(h(liquidation) < h(healthy), "liquidation must score below active");
  assert.equal(h(unknown), 7.5); // unknown → neutral 0.5
});

test("baseline all-zero / unknown-health case → 22.5 (regression guard)", () => {
  const r = computeScore(inputs({ company_health: { ok: false } }), weights);
  // 0 tenders + 0 defense + full competitor(15) + 0 value + neutral health(7.5)
  assert.equal(r.composite, 22.5);
});
