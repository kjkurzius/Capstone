/**
 * opentable-linker.ts
 *
 * OpenTable has NO public consumer booking API, so we cannot make a
 * reservation programmatically. Instead we generate a pre-filled "restref"
 * deep link that opens OpenTable with the date/time/party-size populated, and
 * the human completes the booking.
 *
 * Format (documented restref client link):
 *   https://www.opentable.com/restref/client/?rid=RID&datetime=YYYY-MM-DDTHH:MM&covers=N
 */

export interface OpenTableRequest {
  /** OpenTable restaurant id (rid). Required for a pre-filled link. */
  rid: string;
  /** Local date/time of the desired booking. */
  dateTime: Date;
  /** Number of people. */
  covers: number;
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

/** Format a Date as the OpenTable-expected local `YYYY-MM-DDTHH:MM`. */
export function formatOpenTableDateTime(d: Date): string {
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

/** Build a pre-filled OpenTable reservation link. */
export function buildOpenTableLink(req: OpenTableRequest): string {
  if (!req.rid) throw new Error("OpenTable rid is required to build a pre-filled link.");
  if (req.covers < 1) throw new Error("covers must be >= 1.");
  const params = new URLSearchParams({
    rid: req.rid,
    datetime: formatOpenTableDateTime(req.dateTime),
    covers: String(req.covers),
  });
  return `https://www.opentable.com/restref/client/?${params.toString()}`;
}

/**
 * When we don't have a restaurant's rid, fall back to a search URL so the
 * human can pick the venue. (Search has no covers/datetime pre-fill guarantee.)
 */
export function buildOpenTableSearchLink(term: string): string {
  const params = new URLSearchParams({ term });
  return `https://www.opentable.com/s?${params.toString()}`;
}
