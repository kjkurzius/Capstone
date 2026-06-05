/**
 * companies-house.ts
 *
 * Companies House Public Data API — free, requires a free API key.
 * Docs: https://developer.company-information.service.gov.uk
 * Auth: HTTP Basic, API key as the username, empty password.
 *
 * Two reads:
 *   getCompanyHealth(number)   → status + adverse flags for the target (EEI).
 *   countRecentFilings(numbers, days) → new filings across a competitor watchlist.
 *
 * Defensive: returns ok=false on error rather than throwing.
 */

import "dotenv/config";

const BASE = "https://api.company-information.service.gov.uk";

function authHeader(): string {
  const key = process.env.COMPANIES_HOUSE_API_KEY ?? "";
  if (!key || key.startsWith("[")) {
    throw new Error("COMPANIES_HOUSE_API_KEY is missing or a placeholder. Set it in .env.");
  }
  // Basic auth: key as username, blank password.
  return "Basic " + Buffer.from(`${key}:`).toString("base64");
}

export interface CompanyHealth {
  ok: boolean;
  note: string;
  companyName?: string;
  status?: string; // e.g. "active", "liquidation", "dissolved"
  hasInsolvencyHistory?: boolean;
  hasOverdueAccounts?: boolean;
  hasOverdueConfirmation?: boolean;
}

interface ChCompany {
  company_name?: string;
  company_status?: string;
  has_insolvency_history?: boolean;
  accounts?: { overdue?: boolean; next_accounts?: { overdue?: boolean } };
  confirmation_statement?: { overdue?: boolean };
}

export async function getCompanyHealth(companyNumber: string): Promise<CompanyHealth> {
  try {
    const resp = await fetch(`${BASE}/company/${encodeURIComponent(companyNumber)}`, {
      headers: { Authorization: authHeader(), Accept: "application/json" },
    });
    if (!resp.ok) {
      return { ok: false, note: `Companies House company ${companyNumber} → ${resp.status}` };
    }
    const c = (await resp.json()) as ChCompany;
    return {
      ok: true,
      note: `Companies House: ${c.company_name ?? companyNumber} status=${c.company_status}`,
      companyName: c.company_name,
      status: c.company_status,
      hasInsolvencyHistory: Boolean(c.has_insolvency_history),
      hasOverdueAccounts: Boolean(c.accounts?.overdue ?? c.accounts?.next_accounts?.overdue),
      hasOverdueConfirmation: Boolean(c.confirmation_statement?.overdue),
    };
  } catch (err) {
    return { ok: false, note: `Companies House fetch failed: ${(err as Error).message}` };
  }
}

export interface FilingsResult {
  ok: boolean;
  note: string;
  /** Total new filings across the watchlist in the window. */
  count: number;
}

interface ChFilingHistory {
  items?: { date?: string }[];
}

export async function countRecentFilings(
  companyNumbers: string[],
  windowDays = 30,
): Promise<FilingsResult> {
  if (!companyNumbers.length) {
    return { ok: true, count: 0, note: "No competitor company numbers configured (neutral)." };
  }
  const cutoff = Date.now() - windowDays * 86_400_000;
  let count = 0;
  const failures: string[] = [];
  for (const num of companyNumbers) {
    try {
      const resp = await fetch(
        `${BASE}/company/${encodeURIComponent(num)}/filing-history?items_per_page=100`,
        { headers: { Authorization: authHeader(), Accept: "application/json" } },
      );
      if (!resp.ok) {
        failures.push(`${num}:${resp.status}`);
        continue;
      }
      const fh = (await resp.json()) as ChFilingHistory;
      for (const item of fh.items ?? []) {
        if (item.date && Date.parse(item.date) >= cutoff) count++;
      }
    } catch (err) {
      failures.push(`${num}:${(err as Error).message}`);
    }
  }
  const ok = failures.length === 0;
  return {
    ok,
    count,
    note: ok
      ? `Companies House: ${count} competitor filings in ${windowDays}d across ${companyNumbers.length} companies.`
      : `Partial: ${count} filings; failures for ${failures.join(", ")}.`,
  };
}
