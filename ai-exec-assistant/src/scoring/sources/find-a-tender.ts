/**
 * find-a-tender.ts
 *
 * Find a Tender Service (FTS) OCDS API — OCDS JSON, Open Government Licence.
 * Docs: https://www.find-tender.service.gov.uk/Developer/Documentation
 *
 * Endpoint: /api/1.0/ocdsReleasePackages?updatedFrom=...&updatedTo=...
 * Returns OCDS release packages; we filter client-side by keyword and detect
 * defense/security notices by CPV prefix (e.g. "35" = security/defence
 * equipment). Defensive: returns { ok:false } on error rather than throwing.
 */

import {
  type NoticeItem,
  type NoticeFetchResult,
  parseKeywords,
  matchesKeywords,
  daysAgo,
} from "./types.js";

const BASE =
  process.env.FIND_A_TENDER_BASE ?? "https://www.find-tender.service.gov.uk";

interface FtsRelease {
  date?: string;
  tender?: {
    title?: string;
    description?: string;
    value?: { amount?: number; currency?: string };
    classification?: { scheme?: string; id?: string };
    items?: { classification?: { scheme?: string; id?: string } }[];
  };
}
interface FtsPackage {
  releases?: FtsRelease[];
  links?: { next?: string };
}

function isoInstant(d: Date): string {
  // FTS expects ISO 8601 with timezone.
  return d.toISOString();
}

export async function fetchFindATender(
  keywordQuery: string,
  windowDays = 7,
): Promise<NoticeFetchResult> {
  const terms = parseKeywords(keywordQuery);
  const from = new Date(Date.now() - windowDays * 86_400_000);
  const url =
    `${BASE}/api/1.0/ocdsReleasePackages` +
    `?updatedFrom=${encodeURIComponent(isoInstant(from))}` +
    `&updatedTo=${encodeURIComponent(isoInstant(new Date()))}` +
    `&limit=100`;

  try {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    if (!resp.ok) {
      return { items: [], ok: false, note: `Find a Tender ${resp.status} from ${url}` };
    }
    const pkg = (await resp.json()) as FtsPackage;
    const releases = pkg.releases ?? [];

    const items: NoticeItem[] = [];
    for (const rel of releases) {
      const t = rel.tender;
      if (!t) continue;
      const published = rel.date ?? "";
      if (daysAgo(published) > windowDays) continue;
      const text = `${t.title ?? ""} ${t.description ?? ""}`;
      if (terms.length && !matchesKeywords(text, terms)) continue;
      const cpv = [
        t.classification?.id,
        ...(t.items ?? []).map((it) => it.classification?.id),
      ].filter((x): x is string => Boolean(x));
      items.push({
        title: t.title ?? "(untitled)",
        published,
        valueGbp: t.value?.amount,
        cpv,
        source: "find-a-tender",
      });
    }
    return { items, ok: true, note: `Find a Tender: ${items.length} matching notices (${windowDays}d).` };
  } catch (err) {
    return { items: [], ok: false, note: `Find a Tender fetch failed: ${(err as Error).message}` };
  }
}

/** True if any CPV code starts with one of the given prefixes (e.g. "35"). */
export function isDefenseSecurity(cpv: string[] | undefined, prefixes: string[]): boolean {
  if (!cpv?.length) return false;
  return cpv.some((code) => prefixes.some((p) => code.replace(/[^0-9]/g, "").startsWith(p)));
}
