#!/usr/bin/env node

import { parseArgs as parseNodeArgs } from "node:util";
import { resolve } from "node:path";
import { readCsv, writeCsvAtomic } from "./lib/csv.js";
import { daysBetween, parseDate } from "./lib/dates.js";
import { finiteNumber, isMain, projectDirectory, sleep } from "./lib/runtime.js";

export const DEFAULT_BASE_URL = "https://data.sec.gov/api/xbrl/companyfacts";
export const DEFAULT_USER_AGENT = "Value Stock Weekly/0.1 (contact: indiepixelorg@outlook.com)";
export const DEFAULT_WORKERS = 4;
export const MAX_WORKERS = 8;
export const DEFAULT_TIMEOUT_MILLISECONDS = 30_000;
export const DEFAULT_RETRIES = 3;
export const HISTORY_SLOTS = 5;

export const ANNUAL_HISTORY_FIELDS = [
  ...Array.from({ length: HISTORY_SLOTS }, (_, slot) => `annual_period_end_${slot}`),
  ...Array.from({ length: HISTORY_SLOTS }, (_, slot) => `annual_filing_date_${slot}`),
  ...["revenue", "operating_income", "net_income", "operating_cash_flow",
    "capital_expenditures", "free_cash_flow", "diluted_eps"]
    .flatMap((metric) => Array.from({ length: HISTORY_SLOTS }, (_, slot) => `${metric}_fy${slot}`)),
];

export const OUTPUT_FIELDS = [
  "ticker", "cik", "name", "sector", "subsector", "revenue_ttm", "operating_income_ttm",
  "net_income_ttm", "operating_cash_flow_ttm", "capital_expenditures_ttm",
  "free_cash_flow_ttm", "diluted_eps_ttm", "cash_and_equivalents", "total_assets",
  "total_liabilities", "stockholders_equity", "total_debt", "shares_outstanding",
  "filing_date", "period_end", "data_source", "source_url", ...ANNUAL_HISTORY_FIELDS, "error",
];

export const FLOW_FACTS = {
  revenue_ttm: [
    "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
    "SalesRevenueGoodsNet", "SalesRevenueServicesNet",
  ],
  operating_income_ttm: ["OperatingIncomeLoss"],
  net_income_ttm: ["ProfitLoss", "NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
  operating_cash_flow_ttm: [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
  ],
  capital_expenditures_ttm: [
    "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
  ],
  diluted_eps_ttm: ["EarningsPerShareDiluted"],
};

export const INSTANT_FACTS = {
  cash_and_equivalents: [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
  ],
  total_assets: ["Assets"],
  total_liabilities: ["Liabilities"],
  stockholders_equity: [
    "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
  ],
  shares_outstanding: ["EntityCommonStockSharesOutstanding"],
};

export const DEBT_TOTAL_FACTS = ["LongTermDebtAndFinanceLeaseObligations", "LongTermDebt"];
export const DEBT_CURRENT_FACTS = [
  "DebtCurrent", "LongTermDebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent",
];
export const DEBT_NONCURRENT_FACTS = [
  "LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
];

export class SECError extends Error {
  constructor(message, statusCode = null) {
    super(message);
    this.name = "SECError";
    this.statusCode = statusCode;
  }
}

export function parseArgs(argv = process.argv.slice(2)) {
  const root = projectDirectory(import.meta.url);
  const { values } = parseNodeArgs({
    args: argv,
    options: {
      universe: { type: "string", default: `${root}/generated/universe.csv` },
      output: { type: "string", default: `${root}/generated/data/latest_snapshot.csv` },
      limit: { type: "string" },
      workers: { type: "string", default: String(DEFAULT_WORKERS) },
      help: { type: "boolean", short: "h" },
    },
  });
  const limit = values.limit === undefined ? null : Number(values.limit);
  const workers = Number(values.workers);
  if (limit !== null && !Number.isInteger(limit)) throw new Error("--limit must be an integer.");
  if (!Number.isInteger(workers)) throw new Error("--workers must be an integer.");
  return {
    universe: resolve(values.universe), output: resolve(values.output), limit, workers,
    help: values.help ?? false,
  };
}

export function loadUniverse(path, limit = null) {
  const { fields, rows } = readCsv(path);
  const required = ["ticker", "cik", "name", "sector"];
  if (rows.length === 0 || required.some((field) => !fields.includes(field))) {
    throw new Error(`Universe must contain these columns: ${required.sort().join(", ")}`);
  }
  const securities = [];
  const seen = new Set();
  for (const row of rows) {
    const ticker = String(row.ticker ?? "").trim();
    const cik = String(row.cik ?? "").replace(/\D/g, "");
    if (!ticker) continue;
    if (seen.has(ticker)) throw new Error(`Duplicate ticker in universe.csv: ${ticker}`);
    if (!cik) throw new Error(`Missing CIK in universe.csv for ticker: ${ticker}`);
    seen.add(ticker);
    securities.push({
      ticker,
      cik: cik.padStart(10, "0"),
      name: String(row.name ?? "").trim(),
      sector: String(row.sector ?? "").trim(),
      subsector: String(row.subsector ?? "").trim(),
    });
  }
  if (limit !== null) {
    if (limit < 1) throw new Error("--limit must be greater than zero.");
    securities.splice(limit);
  }
  if (securities.length === 0) throw new Error("No securities found in the universe.");
  return securities;
}

export class SECClient {
  constructor({
    userAgent,
    baseUrl = DEFAULT_BASE_URL,
    timeout = DEFAULT_TIMEOUT_MILLISECONDS,
    fetcher = fetch,
    sleeper = sleep,
  }) {
    this.userAgent = userAgent;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.timeout = timeout;
    this.fetcher = fetcher;
    this.sleeper = sleeper;
  }

  async getCompanyFacts(cik) {
    const url = `${this.baseUrl}/CIK${cik.padStart(10, "0")}.json`;
    let lastError;
    for (let attempt = 0; attempt < DEFAULT_RETRIES; attempt += 1) {
      try {
        const response = await this.fetcher(url, {
          headers: { "User-Agent": this.userAgent, Accept: "application/json" },
          signal: AbortSignal.timeout(this.timeout),
        });
        if (!response.ok) {
          if ([400, 403, 404].includes(response.status)) {
            throw new SECError(`SEC returned HTTP ${response.status} for CIK ${cik}.`, response.status);
          }
          throw new SECError(`SEC returned HTTP ${response.status} (${response.statusText}).`, response.status);
        }
        const payload = await response.json();
        if (!payload || typeof payload !== "object" || !payload.facts || typeof payload.facts !== "object") {
          throw new SECError("SEC returned an unexpected Company Facts payload.");
        }
        return payload;
      } catch (error) {
        if (error instanceof SECError && [400, 403, 404].includes(error.statusCode)) throw error;
        if (error instanceof SECError && error.message.includes("unexpected")) throw error;
        lastError = error instanceof SECError ? error : new SECError(`SEC request failed: ${error.message}`);
        if (attempt < DEFAULT_RETRIES - 1) await this.sleeper((2 ** attempt) * 1_000);
      }
    }
    throw lastError ?? new SECError("SEC request failed.");
  }
}

export function asNumber(value) {
  if (typeof value === "boolean") return null;
  return finiteNumber(value);
}

export function entryKey(entry) {
  return `${entry.start ?? ""}\u0000${entry.end ?? ""}\u0000${entry.form ?? ""}`;
}

export function compareFiling(left, right) {
  for (const field of ["filed", "end", "accn"]) {
    const comparison = String(left[field] ?? "").localeCompare(String(right[field] ?? ""));
    if (comparison) return comparison;
  }
  return 0;
}

export function factEntries(companyFacts, taxonomy, tags, preferredUnits) {
  const taxonomyFacts = companyFacts?.facts?.[taxonomy];
  if (!taxonomyFacts || typeof taxonomyFacts !== "object" || Array.isArray(taxonomyFacts)) return [];
  for (const tag of tags) {
    const definition = taxonomyFacts[tag];
    if (!definition || typeof definition !== "object") continue;
    const units = definition.units;
    if (!units || typeof units !== "object" || Array.isArray(units)) continue;
    const unitNames = [...preferredUnits.filter((unit) => unit in units), ...Object.keys(units).filter((unit) => !preferredUnits.includes(unit))];
    for (const unit of unitNames) {
      if (!Array.isArray(units[unit])) continue;
      const usable = units[unit]
        .filter((entry) => entry && typeof entry === "object" && asNumber(entry.val) !== null)
        .map((entry) => ({ ...entry, _unit: unit, _tag: tag }));
      if (usable.length) return usable;
    }
  }
  return [];
}

export function factEntriesAllTags(companyFacts, taxonomy, tags, preferredUnits) {
  const taxonomyFacts = companyFacts?.facts?.[taxonomy];
  if (!taxonomyFacts || typeof taxonomyFacts !== "object" || Array.isArray(taxonomyFacts)) return [];
  const entries = [];
  for (const tag of tags) {
    const definition = taxonomyFacts[tag];
    if (!definition || typeof definition !== "object") continue;
    const units = definition.units;
    if (!units || typeof units !== "object" || Array.isArray(units)) continue;
    const unitNames = [...preferredUnits.filter((unit) => unit in units), ...Object.keys(units).filter((unit) => !preferredUnits.includes(unit))];
    for (const unit of unitNames) {
      if (!Array.isArray(units[unit])) continue;
      const usable = units[unit]
        .filter((entry) => entry && typeof entry === "object" && asNumber(entry.val) !== null)
        .map((entry) => ({ ...entry, _unit: unit, _tag: tag }));
      if (usable.length) {
        entries.push(...usable);
        break;
      }
    }
  }
  return entries;
}

export function latestInstant(entries) {
  if (!entries.length) return [null, {}];
  const selected = entries.reduce((latest, entry) => compareFiling(entry, latest) > 0 ? entry : latest);
  return [asNumber(selected.val), selected];
}

export function deduplicatePeriods(entries) {
  const selected = new Map();
  for (const entry of entries) {
    const key = entryKey(entry);
    const previous = selected.get(key);
    if (!previous || compareFiling(entry, previous) > 0) selected.set(key, entry);
  }
  return [...selected.values()];
}

export function durationDays(entry) {
  const start = parseDate(entry.start);
  const end = parseDate(entry.end);
  return start && end ? daysBetween(start, end) + 1 : 0;
}

export function isAnnual(entry) {
  return ["10-K", "20-F"].includes(entry.form) && durationDays(entry) >= 300;
}

export function isQuarterlyOrYtd(entry) {
  const duration = durationDays(entry);
  return entry.form === "10-Q" && duration >= 45 && duration <= 320;
}

export function priorAnnual(annuals, currentEnd, filedBy) {
  const candidates = annuals.filter((entry) => {
    const end = parseDate(entry.end);
    const filed = parseDate(entry.filed);
    return end && filed && end < currentEnd && filed <= filedBy;
  });
  return candidates.length
    ? candidates.reduce((latest, entry) => compareFiling(entry, latest) > 0 ? entry : latest)
    : null;
}

export function previousYtd(ytdEntries, current, filedBy) {
  const currentEnd = parseDate(current.end);
  if (!currentEnd) return null;
  const currentDuration = durationDays(current);
  const currentFp = String(current.fp ?? "");
  const candidates = [];
  for (const entry of ytdEntries) {
    const end = parseDate(entry.end);
    const filed = parseDate(entry.filed);
    if (!end || !filed || filed > filedBy || end >= currentEnd) continue;
    if (Math.abs(durationDays(entry) - currentDuration) > 45) continue;
    if (currentFp && entry.fp && entry.fp !== currentFp) continue;
    candidates.push({ distance: Math.abs(daysBetween(end, currentEnd) - 365), entry });
  }
  candidates.sort((left, right) => left.distance - right.distance || -compareFiling(left.entry, right.entry));
  return candidates[0]?.entry ?? null;
}

export function latestFilingPeriod(entries) {
  const usable = entries.filter((entry) => ["10-Q", "10-K", "20-F"].includes(entry.form) && parseDate(entry.filed));
  return usable.length
    ? usable.reduce((latest, entry) => compareFiling(entry, latest) > 0 ? entry : latest)
    : null;
}

export function ttmFlow(sourceEntries) {
  const entries = deduplicatePeriods(sourceEntries.filter((entry) => ["10-Q", "10-K", "20-F"].includes(entry.form)));
  if (!entries.length) return [null, {}];
  const latest = latestFilingPeriod(entries);
  if (!latest) return [null, {}];
  const latestFiled = parseDate(latest.filed);
  const latestEnd = parseDate(latest.end);
  const annuals = entries.filter(isAnnual);
  const ytdEntries = entries.filter(isQuarterlyOrYtd);
  if (latestEnd && isAnnual(latest)) return [asNumber(latest.val), latest];

  if (latestEnd && latest.form === "10-Q") {
    const sameFiling = ytdEntries.filter((entry) => entry.accn === latest.accn && parseDate(entry.filed) === latestFiled);
    const candidates = sameFiling.length ? sameFiling : [latest];
    const currentYtd = candidates.reduce((longest, entry) => durationDays(entry) > durationDays(longest) ? entry : longest);
    const currentValue = asNumber(currentYtd.val);
    const currentEnd = parseDate(currentYtd.end);
    if (currentValue !== null && currentEnd) {
      const priorYtd = previousYtd(ytdEntries, currentYtd, latestFiled);
      const annual = priorAnnual(annuals, currentEnd, latestFiled);
      const priorValue = priorYtd ? asNumber(priorYtd.val) : null;
      const annualValue = annual ? asNumber(annual.val) : null;
      if (annualValue !== null && priorValue !== null) {
        return [annualValue + currentValue - priorValue, currentYtd];
      }
    }
  }

  const quarterEntries = ytdEntries.filter((entry) => durationDays(entry) <= 130 && [undefined, null, 1].includes(entry.qtrs));
  const byEnd = new Map();
  for (const entry of quarterEntries) {
    const end = parseDate(entry.end);
    if (end && (!byEnd.has(end) || compareFiling(entry, byEnd.get(end)) > 0)) byEnd.set(end, entry);
  }
  const recent = [...byEnd.values()].sort(compareFiling).slice(-4);
  if (recent.length === 4) {
    const values = recent.map((entry) => asNumber(entry.val));
    if (values.every((value) => value !== null)) return [values.reduce((sum, value) => sum + value, 0), recent.at(-1)];
  }
  return [null, latest];
}

export function annualHistory(entries, slots = HISTORY_SLOTS) {
  const annualEntries = entries.filter((entry) => (
    ["10-K", "20-F"].includes(entry.form) && durationDays(entry) >= 300 && parseDate(entry.end)
  ));
  const byPeriodEnd = new Map();
  for (const entry of annualEntries) {
    const periodEnd = parseDate(entry.end);
    const previous = byPeriodEnd.get(periodEnd);
    if (
      !previous || durationDays(entry) > durationDays(previous)
      || (durationDays(entry) === durationDays(previous) && compareFiling(entry, previous) > 0)
    ) byPeriodEnd.set(periodEnd, entry);
  }
  return [...byPeriodEnd.values()]
    .sort((left, right) => String(right.end).localeCompare(String(left.end)))
    .slice(0, slots);
}

export function selectedFact(companyFacts, taxonomy, tags, units) {
  return latestInstant(factEntries(companyFacts, taxonomy, tags, units));
}

export function selectedTtmFact(companyFacts, tags, units) {
  return ttmFlow(factEntries(companyFacts, "us-gaap", tags, units));
}

export function selectedAnnualFactHistory(companyFacts, tags, units) {
  return annualHistory(factEntriesAllTags(companyFacts, "us-gaap", tags, units));
}

export function buildFinancials(companyFacts) {
  const values = {};
  const evidence = [];
  const annualHistories = {};
  const annualPeriods = new Set();
  for (const [field, tags] of Object.entries(FLOW_FACTS)) {
    const units = field === "diluted_eps_ttm" ? ["USD/shares"] : ["USD"];
    const annualField = field.slice(0, -4);
    const history = selectedAnnualFactHistory(companyFacts, tags, units);
    annualHistories[annualField] = history;
    for (const entry of history) {
      const periodEnd = parseDate(entry.end);
      if (periodEnd) annualPeriods.add(periodEnd);
    }
  }

  const orderedPeriods = [...annualPeriods].sort().reverse().slice(0, HISTORY_SLOTS);
  const annualSlots = orderedPeriods.map((periodEnd) => ({ period_end: periodEnd, filing_date: "" }));
  while (annualSlots.length < HISTORY_SLOTS) annualSlots.push({ period_end: "", filing_date: "" });

  for (const [field, tags] of Object.entries(FLOW_FACTS)) {
    const units = field === "diluted_eps_ttm" ? ["USD/shares"] : ["USD"];
    const [value, entry] = selectedTtmFact(companyFacts, tags, units);
    values[field] = value ?? "";
    if (Object.keys(entry).length) evidence.push(entry);
    const annualField = field.slice(0, -4);
    const entriesByPeriod = new Map(annualHistories[annualField]
      .map((annualEntry) => [parseDate(annualEntry.end), annualEntry])
      .filter(([periodEnd]) => periodEnd));
    orderedPeriods.forEach((periodEnd, slot) => {
      const annualEntry = entriesByPeriod.get(periodEnd);
      if (!annualEntry) return;
      let annualValue = asNumber(annualEntry.val);
      if (annualField === "capital_expenditures" && annualValue !== null) annualValue = Math.abs(annualValue);
      annualSlots[slot][annualField] = annualValue ?? "";
      const filingDate = parseDate(annualEntry.filed);
      if (filingDate && (!annualSlots[slot].filing_date || filingDate > annualSlots[slot].filing_date)) {
        annualSlots[slot].filing_date = filingDate;
      }
    });
  }

  for (const [field, tags] of Object.entries(INSTANT_FACTS)) {
    const taxonomy = field === "shares_outstanding" ? "dei" : "us-gaap";
    const units = field === "shares_outstanding" ? ["shares"] : ["USD"];
    const [value, entry] = selectedFact(companyFacts, taxonomy, tags, units);
    values[field] = value ?? "";
    if (Object.keys(entry).length) evidence.push(entry);
  }

  const [currentDebt, currentEntry] = selectedFact(companyFacts, "us-gaap", DEBT_CURRENT_FACTS, ["USD"]);
  const [noncurrentDebt, noncurrentEntry] = selectedFact(companyFacts, "us-gaap", DEBT_NONCURRENT_FACTS, ["USD"]);
  let totalDebt;
  if (currentDebt !== null && noncurrentDebt !== null) {
    totalDebt = currentDebt + noncurrentDebt;
    if (Object.keys(currentEntry).length) evidence.push(currentEntry);
    if (Object.keys(noncurrentEntry).length) evidence.push(noncurrentEntry);
  } else {
    let debtEntry;
    [totalDebt, debtEntry] = selectedFact(companyFacts, "us-gaap", DEBT_TOTAL_FACTS, ["USD"]);
    if (totalDebt === null) {
      const components = [currentDebt, noncurrentDebt].filter((value) => value !== null);
      totalDebt = components.length ? components.reduce((sum, value) => sum + value, 0) : null;
      if (Object.keys(currentEntry).length) evidence.push(currentEntry);
      if (Object.keys(noncurrentEntry).length) evidence.push(noncurrentEntry);
    } else if (Object.keys(debtEntry).length) evidence.push(debtEntry);
  }
  values.total_debt = totalDebt ?? "";

  if (!values.total_liabilities) {
    const [liabilitiesAndEquity, liabilityEntry] = selectedFact(
      companyFacts, "us-gaap", ["LiabilitiesAndStockholdersEquity"], ["USD"],
    );
    const equity = asNumber(values.stockholders_equity);
    if (liabilitiesAndEquity !== null && equity !== null) {
      values.total_liabilities = liabilitiesAndEquity - equity;
      if (Object.keys(liabilityEntry).length) evidence.push(liabilityEntry);
    }
  }

  const operatingCashFlow = asNumber(values.operating_cash_flow_ttm);
  let capex = asNumber(values.capital_expenditures_ttm);
  if (capex !== null) {
    capex = Math.abs(capex);
    values.capital_expenditures_ttm = capex;
  }
  values.free_cash_flow_ttm = operatingCashFlow !== null && capex !== null ? operatingCashFlow - capex : "";

  annualSlots.forEach((annualValues, slot) => {
    const annualOperatingCashFlow = asNumber(annualValues.operating_cash_flow);
    const annualCapex = asNumber(annualValues.capital_expenditures);
    if (annualOperatingCashFlow !== null && annualCapex !== null) {
      annualValues.free_cash_flow = annualOperatingCashFlow - annualCapex;
    }
    values[`annual_period_end_${slot}`] = annualValues.period_end ?? "";
    values[`annual_filing_date_${slot}`] = annualValues.filing_date ?? "";
    for (const metric of [
      "revenue", "operating_income", "net_income", "operating_cash_flow",
      "capital_expenditures", "free_cash_flow", "diluted_eps",
    ]) values[`${metric}_fy${slot}`] = annualValues[metric] ?? "";
  });

  const filedDates = evidence.map((entry) => parseDate(entry.filed)).filter(Boolean).sort();
  const periodEnds = evidence.map((entry) => parseDate(entry.end)).filter(Boolean).sort();
  values.filing_date = filedDates.at(-1) ?? "";
  values.period_end = periodEnds.at(-1) ?? "";
  values.error = "";
  return values;
}

export function snapshotRow(security, financials, sourceUrl) {
  const row = { ...security };
  for (const field of OUTPUT_FIELDS) {
    if (!(field in security)) row[field] = financials[field] ?? "";
  }
  row.data_source = "SEC Company Facts";
  row.source_url = sourceUrl;
  row.error = financials.error ?? "";
  return row;
}

async function mapWithConcurrency(items, concurrency, operation) {
  const results = new Array(items.length);
  let nextIndex = 0;
  async function worker() {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await operation(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
  return results;
}

export async function main(argv = process.argv.slice(2)) {
  let args;
  try {
    args = parseArgs(argv);
  } catch (error) {
    console.error(`Error: ${error.message}`);
    return 1;
  }
  if (args.help) {
    console.log("Usage: node scripts/update_snapshot.js [--universe PATH] [--output PATH] [--limit N] [--workers N]");
    return 0;
  }
  if (args.workers < 1 || args.workers > MAX_WORKERS) {
    console.error(`Error: --workers must be between 1 and ${MAX_WORKERS}.`);
    return 1;
  }

  try {
    const securities = loadUniverse(args.universe, args.limit);
    const client = new SECClient({
      userAgent: DEFAULT_USER_AGENT,
      baseUrl: process.env.SEC_BASE_URL ?? DEFAULT_BASE_URL,
    });
    let completed = 0;
    let failedCompanies = 0;
    console.log(`Fetching SEC Company Facts for ${securities.length} securities...`);
    const financials = await mapWithConcurrency(securities, args.workers, async (security) => {
      let result;
      try {
        result = buildFinancials(await client.getCompanyFacts(security.cik));
      } catch (error) {
        if (error instanceof SECError && ![400, 404].includes(error.statusCode)) throw error;
        result = { error: error.message };
        failedCompanies += 1;
      }
      completed += 1;
      if (completed % 25 === 0 || completed === securities.length) {
        console.log(`  companies: ${completed}/${securities.length}`);
      }
      return result;
    });

    const rows = securities.map((security, index) => snapshotRow(
      security,
      financials[index] ?? {},
      `https://data.sec.gov/api/xbrl/companyfacts/CIK${security.cik}.json`,
    ));
    writeCsvAtomic(args.output, rows, OUTPUT_FIELDS);
    console.log(`Wrote ${rows.length} rows to ${args.output}`);
    if (failedCompanies) console.log(`Warning: ${failedCompanies} securities had SEC data errors.`);
    return 0;
  } catch (error) {
    console.error(`Error: ${error.message}`);
    console.error("The existing snapshot file was not changed.");
    return 1;
  }
}

if (isMain(import.meta.url)) process.exitCode = await main();
