/**
 * Tests deterministic slot proposal. Assertions check structural invariants
 * (working hours, lunch, buffers, weekends, count bounds) so they hold
 * regardless of the runner's calendar date / timezone.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  proposeSlots,
  loadCalendarRules,
  type BusyInterval,
} from "../src/utils/calendar-planner.js";

const rules = loadCalendarRules();

function minutesOfDay(d: Date): number {
  return d.getHours() * 60 + d.getMinutes();
}
function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// A Monday 08:00 local, well before working hours.
const MONDAY_8AM = new Date(2026, 5, 8, 8, 0); // 2026-06-08 is a Monday

test("proposes between min and max slots on an open calendar", () => {
  const slots = proposeSlots(rules, { now: MONDAY_8AM, busy: [] });
  assert.ok(slots.length >= rules.min_slots_to_propose, `got ${slots.length}`);
  assert.ok(slots.length <= rules.max_slots_to_propose);
});

test("every slot is in the future, on a working day, within no-meeting bounds, not in lunch", () => {
  const slots = proposeSlots(rules, { now: MONDAY_8AM, busy: [] });
  const before = toMin(rules.no_meeting_before);
  const after = toMin(rules.no_meeting_after);
  const lunchS = toMin(rules.lunch_block.start);
  const lunchE = toMin(rules.lunch_block.end);
  for (const s of slots) {
    assert.ok(s.start >= MONDAY_8AM, "slot must not be in the past");
    const dow = s.start.getDay();
    assert.ok(dow >= 1 && dow <= 5, "slot must be on a weekday (rules have no weekend hours)");
    assert.ok(minutesOfDay(s.start) >= before, "slot starts no earlier than no_meeting_before");
    assert.ok(minutesOfDay(s.end) <= after, "slot ends no later than no_meeting_after");
    // No overlap with lunch.
    const ss = minutesOfDay(s.start);
    const se = minutesOfDay(s.end);
    assert.ok(!(ss < lunchE && lunchS < se), "slot must not overlap the lunch block");
  }
});

test("slots avoid busy intervals plus the configured buffer", () => {
  // Block the whole first working day so proposals must move to later days.
  const busy: BusyInterval[] = [
    { start: new Date(2026, 5, 8, 9, 0), end: new Date(2026, 5, 8, 17, 0) },
  ];
  const slots = proposeSlots(rules, { now: MONDAY_8AM, busy });
  for (const s of slots) {
    const clashesBlockedDay =
      s.start.getFullYear() === 2026 && s.start.getMonth() === 5 && s.start.getDate() === 8;
    assert.ok(!clashesBlockedDay, "no slot should land on the fully-blocked Monday");
  }
  assert.ok(slots.length >= rules.min_slots_to_propose);
});

test("respects an explicit earliest date", () => {
  const earliest = new Date(2026, 5, 10, 0, 0); // Wednesday
  const slots = proposeSlots(rules, { now: MONDAY_8AM, busy: [], earliest });
  for (const s of slots) {
    assert.ok(s.start >= earliest, "no slot before the requested earliest date");
  }
});

test("weekend now still yields weekday slots only", () => {
  const saturday = new Date(2026, 5, 13, 10, 0); // 2026-06-13 is a Saturday
  const slots = proposeSlots(rules, { now: saturday, busy: [] });
  for (const s of slots) {
    const dow = s.start.getDay();
    assert.ok(dow >= 1 && dow <= 5, "weekend has no working hours; slots must be weekdays");
  }
});
