#!/usr/bin/env node

import { parseArgs as parseNodeArgs } from "node:util";
import { resolve } from "node:path";
import { readCsv, writeCsvAtomic } from "./lib/csv.js";
import { daysBetween, parseDate } from "./lib/dates.js";
import { finiteNumber, isMain, projectDirectory, unique } from "./lib/runtime.js";

export const HISTORY_SLOTS = 5;
export const MIN_MEDIAN_YEARS = 3;

export const OUTPUT_FIELDS = [
  "ticker", "cik", "name", "sector", "subsector", "valuation_price", "price_date",
  "price_observations", "price_status", "price_reason", "filing_date", "period_end",
  "revenue_ttm", "operating_income_ttm", "net_income_ttm", "free_cash_flow_ttm",
  "cash_and_equivalents", "total_debt", "stockholders_equity", "shares_outstanding",
  "market_cap", "net_debt", "enterprise_value", "earnings_yield", "free_cash_flow_yield",
  "price_to_earnings", "price_to_fcf", "ev_to_operating_income", "operating_margin",
  "five_year_revenue_growth", "positive_fcf_years", "historical_fcf_years",
  "net_debt_to_fcf", "price_to_book", "five_year_median_fcf",
  "historical_net_income_years", "five_year_median_net_income", "calculation_status",
  "calculation_warnings", "snapshot_source_url", "price_source_url",
];

export const RAW_FINANCIAL_FIELDS = [
  "revenue_ttm", "operating_income_ttm", "net_income_ttm", "free_cash_flow_ttm",
  "cash_and_equivalents", "total_debt", "stockholders_equity", "shares_outstanding",
];

export function parseArgs(argv = process.argv.slice(2)) {
  const root = projectDirectory(import.meta.url);
  const { values } = parseNodeArgs({
    args: argv,
    options: {
      universe: { type: "string", default: `${root}/generated/universe.csv` },
      snapshot: { type: "string", default: `${root}/generated/data/latest_snapshot.csv` },
      prices: { type: "string", default: `${root}/generated/data/latest_prices.csv` },
      output: { type: "string", default: `${root}/generated/data/latest_screen.csv` },
      help: { type: "boolean", short: "h" },
    },
  });
  return {
    universe: resolve(values.universe),
    snapshot: resolve(values.snapshot),
    prices: resolve(values.prices),
    output: resolve(values.output),
    help: values.help ?? false,
  };
}

export function loadCsv(path, requiredFields) {
  const { fields, rows } = readCsv(path);
  const missing = [...requiredFields].filter((field) => !fields.includes(field)).sort();
  if (rows.length === 0 || missing.length) {
    throw new Error(`${path} is empty or missing required columns: ${missing.join(", ")}`);
  }
  return rows;
}

export function indexRows(rows, label) {
  const indexed = new Map();
  for (const row of rows) {
    const ticker = String(row.ticker ?? "").trim();
    if (!ticker) throw new Error(`${label} contains a row without a ticker.`);
    if (indexed.has(ticker)) throw new Error(`${label} contains duplicate ticker: ${ticker}`);
    indexed.set(ticker, row);
  }
  return indexed;
}

export function requireSameTickers(universe, indexed, label) {
  const universeTickers = new Set(universe.map((row) => row.ticker.trim()));
  const missing = [...universeTickers].filter((ticker) => !indexed.has(ticker)).sort();
  const extra = [...indexed.keys()].filter((ticker) => !universeTickers.has(ticker)).sort();
  if (missing.length || extra.length) {
    const details = [];
    if (missing.length) details.push(`missing ${missing.length} (${missing.slice(0, 5).join(", ")})`);
    if (extra.length) details.push(`extra ${extra.length} (${extra.slice(0, 5).join(", ")})`);
    throw new Error(`${label} ticker set does not match universe.csv: ${details.join("; ")}`);
  }
}

export function asNumber(value) {
  return finiteNumber(value);
}

function fixedWithoutZeros(value, decimals) {
  const text = value.toFixed(decimals).replace(/\.?0+$/, "");
  return text === "-0" || text === "" ? "0" : text;
}

export function formatAmount(value) {
  return value === null ? "" : fixedWithoutZeros(value, 6);
}

export function formatRatio(value) {
  if (value === null) return "";
  const rounded = Number(value.toPrecision(15));
  const absolute = Math.abs(rounded);
  const text = absolute > 0 && absolute < 1e-6
    ? rounded.toFixed(20).replace(/\.?0+$/, "")
    : rounded.toLocaleString("en-US", {
      useGrouping: false,
      maximumFractionDigits: 20,
    });
  return text === "-0" || text === "" ? "0" : text;
}

export function divide(numerator, denominator) {
  return numerator === null || denominator === null || denominator === 0
    ? null
    : numerator / denominator;
}

export function positiveDenominatorRatio(numerator, denominator) {
  return denominator === null || denominator <= 0 ? null : divide(numerator, denominator);
}

export function annualValues(snapshot, field) {
  const values = [];
  for (let slot = 0; slot < HISTORY_SLOTS; slot += 1) {
    const value = asNumber(snapshot[`${field}_fy${slot}`]);
    if (value !== null) values.push(value);
  }
  return values;
}

export function median(values) {
  const ordered = [...values].sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

export function historicalMedian(values) {
  return values.length < MIN_MEDIAN_YEARS ? null : median(values);
}

export function revenueCagr(snapshot) {
  const newest = asNumber(snapshot.revenue_fy0);
  const oldest = asNumber(snapshot.revenue_fy4);
  const newestDate = parseDate(snapshot.annual_period_end_0);
  const oldestDate = parseDate(snapshot.annual_period_end_4);
  if (
    newest === null || oldest === null || newest <= 0 || oldest <= 0
    || !newestDate || !oldestDate || newestDate <= oldestDate
  ) return null;
  const years = daysBetween(oldestDate, newestDate) / 365.2425;
  if (years <= 0) return null;
  const growth = (newest / oldest) ** (1 / years) - 1;
  return Number.isFinite(growth) ? growth : null;
}

export function addInputWarning(warnings, field, value, requirePositive) {
  if (value === null) warnings.push(`missing_${field}`);
  else if (requirePositive && value <= 0) warnings.push(`nonpositive_${field}`);
}

export function buildRow(universe, snapshot, price) {
  const row = {
    ticker: universe.ticker.trim(),
    cik: String(universe.cik ?? "").trim(),
    name: String(universe.name ?? "").trim(),
    sector: String(universe.sector ?? "").trim(),
    subsector: String(universe.subsector ?? "").trim(),
    valuation_price: String(price.valuation_price ?? "").trim(),
    price_date: String(price.price_date ?? "").trim(),
    price_observations: String(price.observations ?? "").trim(),
    price_status: String(price.status ?? "").trim(),
    price_reason: String(price.reason ?? "").trim(),
    filing_date: String(snapshot.filing_date ?? "").trim(),
    period_end: String(snapshot.period_end ?? "").trim(),
    snapshot_source_url: String(snapshot.source_url ?? "").trim(),
    price_source_url: String(price.source_url ?? "").trim(),
  };
  for (const field of RAW_FINANCIAL_FIELDS) row[field] = String(snapshot[field] ?? "").trim();

  if (row.price_status !== "ok") {
    row.calculation_status = "excluded";
    row.calculation_warnings = row.price_reason || "price_unavailable";
    return Object.fromEntries(OUTPUT_FIELDS.map((field) => [field, row[field] ?? ""]));
  }

  const valuationPrice = asNumber(row.valuation_price);
  const shares = asNumber(row.shares_outstanding);
  const revenue = asNumber(row.revenue_ttm);
  const operatingIncome = asNumber(row.operating_income_ttm);
  const netIncome = asNumber(row.net_income_ttm);
  const freeCashFlow = asNumber(row.free_cash_flow_ttm);
  const cash = asNumber(row.cash_and_equivalents);
  const debt = asNumber(row.total_debt);
  const equity = asNumber(row.stockholders_equity);

  const warnings = [];
  for (const [field, value, requirePositive] of [
    ["valuation_price", valuationPrice, true], ["shares_outstanding", shares, true],
    ["revenue_ttm", revenue, true], ["operating_income_ttm", operatingIncome, true],
    ["net_income_ttm", netIncome, true], ["free_cash_flow_ttm", freeCashFlow, true],
    ["cash_and_equivalents", cash, false], ["total_debt", debt, false],
    ["stockholders_equity", equity, true],
  ]) addInputWarning(warnings, field, value, requirePositive);

  const marketCap = valuationPrice !== null && valuationPrice > 0 && shares !== null && shares > 0
    ? valuationPrice * shares : null;
  const netDebt = debt !== null && cash !== null ? debt - cash : null;
  const enterpriseValue = marketCap !== null && netDebt !== null ? marketCap + netDebt : null;
  const earningsYield = positiveDenominatorRatio(netIncome, marketCap);
  const freeCashFlowYield = positiveDenominatorRatio(freeCashFlow, marketCap);
  const priceToEarnings = positiveDenominatorRatio(marketCap, netIncome);
  const priceToFcf = positiveDenominatorRatio(marketCap, freeCashFlow);
  const evToOperatingIncome = enterpriseValue !== null && enterpriseValue > 0
    ? positiveDenominatorRatio(enterpriseValue, operatingIncome) : null;
  const operatingMargin = positiveDenominatorRatio(operatingIncome, revenue);
  const netDebtToFcf = positiveDenominatorRatio(netDebt, freeCashFlow);
  const priceToBook = positiveDenominatorRatio(marketCap, equity);

  const revenueGrowth = revenueCagr(snapshot);
  if (revenueGrowth === null) warnings.push("insufficient_revenue_history");
  const fcfHistory = annualValues(snapshot, "free_cash_flow");
  const netIncomeHistory = annualValues(snapshot, "net_income");
  const medianFcf = historicalMedian(fcfHistory);
  const medianNetIncome = historicalMedian(netIncomeHistory);
  if (medianFcf === null) warnings.push("insufficient_fcf_history");
  if (medianNetIncome === null) warnings.push("insufficient_net_income_history");

  Object.assign(row, {
    market_cap: formatAmount(marketCap),
    net_debt: formatAmount(netDebt),
    enterprise_value: formatAmount(enterpriseValue),
    earnings_yield: formatRatio(earningsYield),
    free_cash_flow_yield: formatRatio(freeCashFlowYield),
    price_to_earnings: formatRatio(priceToEarnings),
    price_to_fcf: formatRatio(priceToFcf),
    ev_to_operating_income: formatRatio(evToOperatingIncome),
    operating_margin: formatRatio(operatingMargin),
    five_year_revenue_growth: formatRatio(revenueGrowth),
    positive_fcf_years: fcfHistory.length ? fcfHistory.filter((value) => value > 0).length : "",
    historical_fcf_years: fcfHistory.length,
    net_debt_to_fcf: formatRatio(netDebtToFcf),
    price_to_book: formatRatio(priceToBook),
    five_year_median_fcf: formatAmount(medianFcf),
    historical_net_income_years: netIncomeHistory.length,
    five_year_median_net_income: formatAmount(medianNetIncome),
    calculation_status: warnings.length ? "partial" : "ok",
    calculation_warnings: unique(warnings).join(";"),
  });
  return Object.fromEntries(OUTPUT_FIELDS.map((field) => [field, row[field] ?? ""]));
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv);
    if (args.help) {
      console.log("Usage: node scripts/build_screen.js [--universe PATH] [--snapshot PATH] [--prices PATH] [--output PATH]");
      return 0;
    }
    const universe = loadCsv(args.universe, new Set(["ticker", "cik", "name", "sector", "subsector"]));
    const snapshots = indexRows(
      loadCsv(args.snapshot, new Set(["ticker", ...RAW_FINANCIAL_FIELDS])),
      "latest_snapshot.csv",
    );
    const prices = indexRows(
      loadCsv(args.prices, new Set(["ticker", "valuation_price", "status", "reason"])),
      "latest_prices.csv",
    );
    requireSameTickers(universe, snapshots, "latest_snapshot.csv");
    requireSameTickers(universe, prices, "latest_prices.csv");
    const rows = universe.map((row) => buildRow(row, snapshots.get(row.ticker.trim()), prices.get(row.ticker.trim())));
    writeCsvAtomic(args.output, rows, OUTPUT_FIELDS);
    console.log(`Wrote ${rows.length} rows to ${args.output}`);
    for (const status of ["ok", "partial", "excluded"]) {
      const count = rows.filter((row) => row.calculation_status === status).length;
      if (count) console.log(`  ${status}: ${count}`);
    }
    return 0;
  } catch (error) {
    console.error(`Error: ${error.message}`);
    console.error("The existing screen file was not changed.");
    return 1;
  }
}

if (isMain(import.meta.url)) process.exitCode = await main();
