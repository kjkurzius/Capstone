/**
 * Tests OpenTable link generation — the format must match the documented
 * restref deep link exactly, and validation must reject bad input.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  formatOpenTableDateTime,
  buildOpenTableLink,
  buildOpenTableSearchLink,
} from "../src/utils/opentable-linker.js";

test("formatOpenTableDateTime → YYYY-MM-DDTHH:MM (local components, zero-padded)", () => {
  // Constructed with local components; formatter also reads local components,
  // so this is timezone-independent.
  const d = new Date(2026, 5, 9, 7, 5); // 2026-06-09 07:05 local
  assert.equal(formatOpenTableDateTime(d), "2026-06-09T07:05");
});

test("buildOpenTableLink produces a valid pre-filled restref URL", () => {
  const url = buildOpenTableLink({ rid: "12345", dateTime: new Date(2026, 5, 9, 19, 30), covers: 4 });
  const u = new URL(url);
  assert.equal(u.origin + u.pathname, "https://www.opentable.com/restref/client/");
  assert.equal(u.searchParams.get("rid"), "12345");
  assert.equal(u.searchParams.get("datetime"), "2026-06-09T19:30");
  assert.equal(u.searchParams.get("covers"), "4");
});

test("buildOpenTableLink validates rid and covers", () => {
  assert.throws(() => buildOpenTableLink({ rid: "", dateTime: new Date(), covers: 2 }), /rid is required/);
  assert.throws(
    () => buildOpenTableLink({ rid: "1", dateTime: new Date(), covers: 0 }),
    /covers must be/,
  );
});

test("search fallback encodes the term", () => {
  const url = buildOpenTableSearchLink("The Ledbury, London");
  const u = new URL(url);
  assert.equal(u.origin + u.pathname, "https://www.opentable.com/s");
  assert.equal(u.searchParams.get("term"), "The Ledbury, London");
});
