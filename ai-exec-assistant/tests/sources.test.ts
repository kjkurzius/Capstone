/**
 * Tests the pure helpers the source fetchers depend on: keyword parsing/
 * matching, recency, and CPV-based defense/security detection. These are the
 * bits that decide which notices count, so correctness here is load-bearing.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseKeywords, matchesKeywords, daysAgo } from "../src/scoring/sources/types.js";
import { isDefenseSecurity } from "../src/scoring/sources/find-a-tender.js";

test("parseKeywords splits on OR and strips quotes/case", () => {
  const terms = parseKeywords('drone OR RPAS OR "unmanned aircraft" OR \'aerial survey\'');
  assert.deepEqual(terms, ["drone", "rpas", "unmanned aircraft", "aerial survey"]);
});

test("matchesKeywords is case-insensitive and matches any term", () => {
  const terms = parseKeywords('drone OR "aerial survey"');
  assert.equal(matchesKeywords("Supply of DRONE inspection services", terms), true);
  assert.equal(matchesKeywords("Aerial Survey of coastline", terms), true);
  assert.equal(matchesKeywords("Catering framework agreement", terms), false);
});

test("daysAgo: recent ~0, old large, invalid → Infinity", () => {
  const now = new Date().toISOString();
  assert.ok(daysAgo(now) < 1);
  const tenDaysAgo = new Date(Date.now() - 10 * 86_400_000).toISOString();
  assert.ok(daysAgo(tenDaysAgo) >= 9.9 && daysAgo(tenDaysAgo) <= 10.1);
  assert.equal(daysAgo("not-a-date"), Number.POSITIVE_INFINITY);
});

test("isDefenseSecurity matches CPV prefix and ignores non-digits", () => {
  assert.equal(isDefenseSecurity(["35600000-3"], ["35"]), true); // security/defence equipment
  assert.equal(isDefenseSecurity(["72000000"], ["35"]), false); // IT services
  assert.equal(isDefenseSecurity([], ["35"]), false);
  assert.equal(isDefenseSecurity(undefined, ["35"]), false);
  assert.equal(isDefenseSecurity(["35100000", "80000000"], ["35"]), true);
});
