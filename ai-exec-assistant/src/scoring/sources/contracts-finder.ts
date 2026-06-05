/**
 * contracts-finder.ts
 *
 * Contracts Finder OCDS REST API — public, Open Government Licence, no key.
 * Docs: https://www.contractsfinder.service.gov.uk/apidocumentation
 *
 * The OCDS search endpoint returns release packages. We request notices
 * published in the last `windowDays`, then filter client-side by the keyword
 * query and extract estimated values + CPV codes.
 *
 * The fetcher is defensive: on any network/schema error it returns
 * { ok: false } with zero items and a note, so the daily score still computes
 * (the report flags degraded data) rather than crashing the pipeline.
 */

import {
  type NoticeItem,
  type NoticeFetchResult,
  parseKeywords,
  matchesKeywords,
  daysAgo,
} from "./types.js";

const BASE =
  process.env.CONTRACTS_FINDER_BASE ??
  "https://www.contractsfinder.service.gov.uk";

interface OcdsRelease {
  tender?: {
    title?: string;
    description?: string;
    value?: { amount?: number; currency?: string };
    items?: { classification?: { scheme?: string; id?: string } }[];
  };
  date?: string;
}
interface OcdsPackage {
  releases?: OcdsRelease[];
  results?: { releases?: OcdsRelease[] }[];
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export async function fetchContractsFinder(
  keywordQuery: string,
  windowDays = 7,
): Promise<NoticeFetchResult> {
  const terms = parseKeywords(keywordQuery);
  const from = new Date(Date.now() - windowDays * 86_400_000);

  // OCDS search: publishedFrom/publishedTo bound the window; we page once.
  const url =
    `${BASE}/Published/Notices/OCDS/Search` +
    `?publishedFrom=${isoDate(from)}&publishedTo=${isoDate(new Date())}&limit=100`;

  try {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    if (!resp.ok) {
      return { items: [], ok: false, note: `Contracts Finder ${resp.status} from ${url}` };
    }
    const pkg = (await resp.json()) as OcdsPackage;

    // Normalise both possible shapes (top-level releases, or results[].releases).
    const releases: OcdsRelease[] = pkg.releases ?? pkg.results?.flatMap((r) => r.releases ?? []) ?? [];

    const items: NoticeItem[] = [];
    for (const rel of releases) {
      const t = rel.tender;
      if (!t) continue;
      const published = rel.date ?? "";
      if (daysAgo(published) > windowDays) continue;
      const text = `${t.title ?? ""} ${t.description ?? ""}`;
      if (terms.length && !matchesKeywords(text, terms)) continue;
      const cpv = (t.items ?? [])
        .map((it) => it.classification?.id)
        .filter((x): x is string => Boolean(x));
      items.push({
        title: t.title ?? "(untitled)",
        published,
        valueGbp:
          t.value?.amount && (!t.value.currency || t.value.currency === "GBP")
            ? t.value.amount
            : t.value?.amount,
        cpv,
        source: "contracts-finder",
      });
    }
    return { items, ok: true, note: `Contracts Finder: ${items.length} matching notices (${windowDays}d).` };
  } catch (err) {
    return { items: [], ok: false, note: `Contracts Finder fetch failed: ${(err as Error).message}` };
  }
}
