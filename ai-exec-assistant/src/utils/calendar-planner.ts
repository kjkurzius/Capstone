/**
 * calendar-planner.ts
 *
 * DETERMINISTIC meeting-slot proposal. Pure function — no LLM, no I/O — so the
 * n8n Code node and the local triage runner share one tested implementation
 * instead of duplicating logic. All policy comes from calendar-rules.json.
 *
 * Works in the runtime's local wall-clock (construct/compare with local Date
 * components). In n8n, set the container/USER_TIMEZONE so "local" == the
 * executive's timezone; the emitted slots are labelled with that timezone.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

interface DayHours {
  start: string; // "HH:MM"
  end: string; // "HH:MM"
}
export interface CalendarRules {
  timezone: string;
  working_hours: Record<string, DayHours | null>;
  default_meeting_minutes: number;
  buffer_minutes_between_meetings: number;
  no_meeting_before: string;
  no_meeting_after: string;
  lunch_block: { start: string; end: string };
  max_slots_to_propose: number;
  min_slots_to_propose: number;
  lookahead_business_days: number;
  avoid_back_to_back: boolean;
}

export interface BusyInterval {
  start: Date;
  end: Date;
}
export interface ProposedSlot {
  start: Date;
  end: Date;
}
export interface PlanOptions {
  now: Date;
  busy: BusyInterval[];
  durationMinutes?: number;
  earliest?: Date;
  latest?: Date;
  slotStepMinutes?: number; // candidate granularity; default 30
}

const DAY_NAMES = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

export function loadCalendarRules(): CalendarRules {
  return JSON.parse(
    readFileSync(resolve(__dirname, "../config/calendar-rules.json"), "utf-8"),
  ) as CalendarRules;
}

/** "HH:MM" → a Date on the same calendar day as `ref`, in local time. */
function atTime(ref: Date, hhmm: string): Date {
  const [h, m] = hhmm.split(":").map(Number);
  return new Date(ref.getFullYear(), ref.getMonth(), ref.getDate(), h, m, 0, 0);
}

function overlaps(aStart: Date, aEnd: Date, bStart: Date, bEnd: Date): boolean {
  return aStart < bEnd && bStart < aEnd;
}

/**
 * Propose between min and max slots that respect working hours, the lunch
 * block, no-meeting bounds, buffers, and existing busy intervals.
 */
export function proposeSlots(rules: CalendarRules, opts: PlanOptions): ProposedSlot[] {
  const duration = opts.durationMinutes ?? rules.default_meeting_minutes;
  const step = opts.slotStepMinutes ?? 30;
  const bufferMs = rules.buffer_minutes_between_meetings * 60_000;
  const slots: ProposedSlot[] = [];

  // Start scanning from the later of now / earliest, but never before now.
  const scanStart = opts.earliest && opts.earliest > opts.now ? opts.earliest : opts.now;
  const hardLatest = opts.latest ?? null;

  let businessDaysSeen = 0;
  const cursorDay = new Date(scanStart.getFullYear(), scanStart.getMonth(), scanStart.getDate());

  while (businessDaysSeen < rules.lookahead_business_days && slots.length < rules.max_slots_to_propose) {
    const dayName = DAY_NAMES[cursorDay.getDay()];
    const hours = rules.working_hours[dayName];
    if (hours) {
      businessDaysSeen++;

      // Effective window = working hours ∩ [no_meeting_before, no_meeting_after].
      const winStartCandidates = [atTime(cursorDay, hours.start), atTime(cursorDay, rules.no_meeting_before)];
      const winEndCandidates = [atTime(cursorDay, hours.end), atTime(cursorDay, rules.no_meeting_after)];
      let winStart = new Date(Math.max(...winStartCandidates.map((d) => d.getTime())));
      const winEnd = new Date(Math.min(...winEndCandidates.map((d) => d.getTime())));

      // Never propose in the past.
      if (winStart < scanStart) winStart = new Date(Math.max(winStart.getTime(), scanStart.getTime()));

      const lunchStart = atTime(cursorDay, rules.lunch_block.start);
      const lunchEnd = atTime(cursorDay, rules.lunch_block.end);

      for (
        let t = new Date(winStart);
        t.getTime() + duration * 60_000 <= winEnd.getTime() && slots.length < rules.max_slots_to_propose;
        t = new Date(t.getTime() + step * 60_000)
      ) {
        const start = new Date(t);
        const end = new Date(t.getTime() + duration * 60_000);

        if (start < opts.now) continue;
        if (hardLatest && start > hardLatest) continue;
        if (overlaps(start, end, lunchStart, lunchEnd)) continue;

        // Buffer + back-to-back check against busy intervals.
        const conflict = opts.busy.some((b) => {
          const bStart = new Date(b.start.getTime() - bufferMs);
          const bEnd = new Date(b.end.getTime() + bufferMs);
          return overlaps(start, end, bStart, bEnd);
        });
        if (conflict) continue;

        slots.push({ start, end });
        // Prefer spreading across days: after taking one slot, jump to next day
        // unless we still need to reach the minimum from a sparse calendar.
        if (slots.length >= rules.min_slots_to_propose) break;
      }
    }
    cursorDay.setDate(cursorDay.getDate() + 1);
  }

  return slots.slice(0, rules.max_slots_to_propose);
}

/** Format a slot for a Graph event body using the configured timezone label. */
export function slotToGraphEvent(slot: ProposedSlot, timezone: string) {
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` +
    `T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:00`;
  return {
    start: { dateTime: iso(slot.start), timeZone: timezone },
    end: { dateTime: iso(slot.end), timeZone: timezone },
  };
}
