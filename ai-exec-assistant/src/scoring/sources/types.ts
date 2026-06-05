/** Shared shapes for scoring source fetchers. */

export interface NoticeItem {
  title: string;
  published: string; // ISO date
  valueGbp?: number;
  cpv?: string[];
  source: "contracts-finder" | "find-a-tender";
}

export interface NoticeFetchResult {
  /** Notices in the last `windowDays` matching the keyword query. */
  items: NoticeItem[];
  /** True if the fetch succeeded; false if the API was unreachable/errored. */
  ok: boolean;
  /** Human-readable note (e.g. why ok=false, or which endpoint was hit). */
  note: string;
}

/** Parse the OR-style keyword query from scoring-weights.json into terms. */
export function parseKeywords(query: string): string[] {
  return query
    .split(/\s+OR\s+/i)
    .map((t) => t.trim().replace(/^["']|["']$/g, "").toLowerCase())
    .filter(Boolean);
}

/** Case-insensitive: does `text` contain any of the keyword terms? */
export function matchesKeywords(text: string, terms: string[]): boolean {
  const hay = text.toLowerCase();
  return terms.some((t) => hay.includes(t));
}

/** Days between now and an ISO date string. */
export function daysAgo(iso: string): number {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return Number.POSITIVE_INFINITY;
  return (Date.now() - t) / 86_400_000;
}
