#!/usr/bin/env node

import { parseArgs as parseNodeArgs } from "node:util";
import { resolve } from "node:path";
import { readCsv, writeCsvAtomic } from "./lib/csv.js";
import { finiteNumber, isMain, projectDirectory, unique } from "./lib/runtime.js";

export const TOP_REQUIRED_FIELDS = new Set([
  "rank", "ticker", "cik", "name", "sector", "subsector", "valuation_price", "price_date",
  "attractiveness_score", "quality_score", "quality_display_score", "quality_label",
  "free_cash_flow_yield", "free_cash_flow_yield_score", "earnings_yield",
  "earnings_yield_score", "ev_to_operating_income", "operating_margin",
  "operating_margin_score", "five_year_revenue_growth", "positive_fcf_years",
  "historical_fcf_years", "net_debt_to_fcf", "selection_reasons", "review_flags",
  "snapshot_source_url", "price_source_url",
]);

export const SCREEN_REQUIRED_FIELDS = new Set([
  "ticker", "cik", "filing_date", "period_end", "net_income_ttm", "free_cash_flow_ttm",
  "total_debt", "stockholders_equity", "five_year_median_fcf", "five_year_median_net_income",
]);

export const OUTPUT_FIELDS = [
  "rank", "ticker", "cik", "name", "sector", "subsector", "valuation_price", "price_date",
  "filing_date", "period_end", "attractiveness_score", "quality_score",
  "quality_display_score", "quality_label", "why_selected", "valuation_summary",
  "business_quality_summary", "growth_summary", "balance_sheet_summary", "warning_summary",
  "what_to_verify", "review_status", "generation_method", "snapshot_source_url", "price_source_url",
];

export const SECTOR_CHECKS = {
  "Communication Services": "capital spending, debt obligations, subscriber or audience trends, and competitive pressure",
  "Consumer Discretionary": "demand cyclicality, pricing power, financing exposure, and inventory trends",
  "Consumer Staples": "volume trends, pricing power, and input-cost pressure",
  Energy: "cash-flow durability across the commodity cycle and the assumptions behind current capital spending",
  "Health Care": "product concentration, reimbursement, regulatory exposure, and the pipeline",
  Industrials: "backlog quality, customer concentration, cyclicality, and margin durability",
  "Information Technology": "competitive pressure, product transitions, customer retention, and the durability of margins",
  Materials: "cash-flow durability across the commodity cycle, input costs, and capacity",
  Utilities: "regulatory outcomes, capital spending, and refinancing needs",
};

export function parseArgs(argv = process.argv.slice(2)) {
  const root = projectDirectory(import.meta.url);
  const { values } = parseNodeArgs({
    args: argv,
    options: {
      top10: { type: "string", default: `${root}/generated/data/latest_top10.csv` },
      screen: { type: "string", default: `${root}/generated/data/latest_screen.csv` },
      output: { type: "string", default: `${root}/generated/data/latest_research.csv` },
      help: { type: "boolean", short: "h" },
    },
  });
  return {
    top10: resolve(values.top10), screen: resolve(values.screen), output: resolve(values.output),
    help: values.help ?? false,
  };
}

export function loadCsv(path, requiredFields, label) {
  const { fields, rows } = readCsv(path);
  const missing = [...requiredFields].filter((field) => !fields.includes(field)).sort();
  if (rows.length === 0 || missing.length) {
    throw new Error(`${label} is empty or missing required columns: ${missing.join(", ")}`);
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

export function number(row, field) {
  const value = finiteNumber(row[field]);
  if (value === null) throw new Error(`${row.ticker || "row"} has invalid ${field}.`);
  return value;
}

export function percent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

export function multiple(value) {
  return `${value.toFixed(1)}x`;
}

export function comparisonDescription(score) {
  if (score >= 80) return "in the top fifth of its comparison group";
  if (score >= 60) return "above the comparison-group median";
  if (score >= 40) return "near the comparison-group median";
  return "below the comparison-group median";
}

export function warningCodes(top, screen) {
  const warnings = top.review_flags.split(";").filter(Boolean);
  const medianNetIncome = number(screen, "five_year_median_net_income");
  const medianFcf = number(screen, "five_year_median_fcf");
  if (number(screen, "net_income_ttm") / medianNetIncome > 1.75) {
    warnings.push("ttm_net_income_well_above_five_year_median");
  }
  if (number(screen, "free_cash_flow_ttm") / medianFcf > 1.75) {
    warnings.push("ttm_fcf_well_above_five_year_median");
  }
  if (number(top, "net_debt_to_fcf") > 4) warnings.push("elevated_net_debt_to_fcf");
  if (number(screen, "total_debt") < number(screen, "stockholders_equity") * 0.10) {
    warnings.push("unusually_low_reported_debt_verify_xbrl_coverage");
  }
  if (number(top, "five_year_revenue_growth") < 0) warnings.push("negative_five_year_revenue_growth");
  return unique(warnings);
}

export function warningText(codes, top, screen) {
  const messages = [];
  if (codes.some((code) => code.startsWith("extreme_"))) {
    messages.push("The reported yields are unusually high, so the share count, implied market capitalization, and possible one-off items require verification.");
  }
  if (codes.includes("ttm_net_income_well_above_five_year_median")) {
    const ratio = number(screen, "net_income_ttm") / number(screen, "five_year_median_net_income");
    messages.push(`TTM net income is ${ratio.toFixed(1)}x the five-year median; confirm how much is recurring.`);
  }
  if (codes.includes("ttm_fcf_well_above_five_year_median")) {
    const ratio = number(screen, "free_cash_flow_ttm") / number(screen, "five_year_median_fcf");
    messages.push(`TTM free cash flow is ${ratio.toFixed(1)}x the five-year median; confirm whether working-capital timing or another temporary factor contributed.`);
  }
  if (codes.includes("elevated_net_debt_to_fcf")) {
    messages.push(`Net debt is ${multiple(number(top, "net_debt_to_fcf"))} trailing free cash flow, which warrants a closer debt review.`);
  }
  if (codes.includes("unusually_low_reported_debt_verify_xbrl_coverage")) {
    messages.push("Reported debt is less than 10% of stockholders' equity; verify that the selected SEC XBRL facts capture all material borrowings and leases.");
  }
  if (codes.includes("negative_five_year_revenue_growth")) {
    messages.push("Revenue contracted over the measured five-year period.");
  }
  if (messages.length === 0) {
    messages.push("No numerical anomaly crossed the current rule-based thresholds. This does not cover qualitative business risks.");
  }
  return messages.join(" ");
}

export function buildNote(top, screen) {
  if (top.cik.trim() !== screen.cik.trim()) throw new Error(`CIK mismatch for ${top.ticker}.`);
  const fcfYield = number(top, "free_cash_flow_yield");
  const earningsYield = number(top, "earnings_yield");
  const evToOperatingIncome = number(top, "ev_to_operating_income");
  const operatingMargin = number(top, "operating_margin");
  const revenueGrowth = number(top, "five_year_revenue_growth");
  const netDebtToFcf = number(top, "net_debt_to_fcf");
  const positiveFcfYears = Math.trunc(number(top, "positive_fcf_years"));
  const historicalFcfYears = Math.trunc(number(top, "historical_fcf_years"));
  const warnings = warningCodes(top, screen);
  const selectedReasons = top.selection_reasons.replace(/[ .]+$/, "");

  const whySelected = `Ranked #${Math.trunc(number(top, "rank"))} with an attractiveness score of ${number(top, "attractiveness_score").toFixed(2)}/100. Its largest quantitative contributors were ${selectedReasons}.`;
  const valuationSummary = `The screen reports a ${percent(fcfYield)} free-cash-flow yield, which is ${comparisonDescription(number(top, "free_cash_flow_yield_score"))}, and a ${percent(earningsYield)} earnings yield, which is ${comparisonDescription(number(top, "earnings_yield_score"))}. Enterprise value is ${multiple(evToOperatingIncome)} operating income.`;
  const businessQualitySummary = `The quantitative quality score is ${number(top, "quality_display_score").toFixed(1)}/10 (${top.quality_label}). Operating margin is ${percent(operatingMargin)}, ${comparisonDescription(number(top, "operating_margin_score"))}. Free cash flow was positive in ${positiveFcfYears} of ${historicalFcfYears} reported fiscal years.`;

  let growthLabel;
  if (revenueGrowth >= 0.08) growthLabel = "solid";
  else if (revenueGrowth >= 0.02) growthLabel = "moderate";
  else if (revenueGrowth >= 0) growthLabel = "modest";
  else growthLabel = "negative";
  const growthSummary = `Revenue changed at approximately ${percent(revenueGrowth)} annually across the measured five-year period, representing ${growthLabel} historical growth.`;

  let balanceSheetSummary;
  if (netDebtToFcf < 0) {
    balanceSheetSummary = `The SEC-derived inputs imply more cash than debt, with net cash equal to ${multiple(Math.abs(netDebtToFcf))} trailing free cash flow.`;
  } else if (netDebtToFcf <= 2) {
    balanceSheetSummary = `Net debt is ${multiple(netDebtToFcf)} trailing free cash flow, a moderate level under this screen.`;
  } else if (netDebtToFcf <= 4) {
    balanceSheetSummary = `Net debt is ${multiple(netDebtToFcf)} trailing free cash flow, making debt service an important review item.`;
  } else {
    balanceSheetSummary = `Net debt is ${multiple(netDebtToFcf)} trailing free cash flow, an elevated level requiring detailed review.`;
  }

  const verificationItems = [];
  if (warnings.some((code) => code.startsWith("extreme_"))) {
    verificationItems.push("shares outstanding and the removal of non-recurring earnings or cash-flow items");
  }
  if (warnings.some((code) => code.includes("well_above_five_year_median"))) {
    verificationItems.push("the reason for the gap between TTM results and normalized history");
  }
  if (warnings.includes("elevated_net_debt_to_fcf")) {
    verificationItems.push("debt maturities, interest expense, and refinancing needs");
  }
  if (warnings.includes("unusually_low_reported_debt_verify_xbrl_coverage")) {
    verificationItems.push("reported debt and lease obligations against the balance sheet notes");
  }
  if (warnings.includes("negative_five_year_revenue_growth")) {
    verificationItems.push("the causes of the historical revenue decline");
  }
  verificationItems.push(SECTOR_CHECKS[top.sector]
    ?? "competitive position, customer concentration, and margin durability");

  return {
    rank: String(Math.trunc(number(top, "rank"))),
    ticker: top.ticker.trim(), cik: top.cik.trim(), name: top.name.trim(),
    sector: top.sector.trim(), subsector: top.subsector.trim(),
    valuation_price: top.valuation_price.trim(), price_date: top.price_date.trim(),
    filing_date: screen.filing_date.trim(), period_end: screen.period_end.trim(),
    attractiveness_score: number(top, "attractiveness_score").toFixed(2),
    quality_score: number(top, "quality_score").toFixed(2),
    quality_display_score: number(top, "quality_display_score").toFixed(1),
    quality_label: top.quality_label.trim(),
    why_selected: whySelected,
    valuation_summary: valuationSummary,
    business_quality_summary: businessQualitySummary,
    growth_summary: growthSummary,
    balance_sheet_summary: balanceSheetSummary,
    warning_summary: warningText(warnings, top, screen),
    what_to_verify: `Before publication, read the latest 10-K or 10-Q and verify ${verificationItems.join("; verify ")}.`,
    review_status: warnings.length ? "priority_review" : "standard_review",
    generation_method: "deterministic_rules_v1",
    snapshot_source_url: top.snapshot_source_url.trim(),
    price_source_url: top.price_source_url.trim(),
  };
}

export function buildResearchNotes(topRows, screenRows) {
  const topIndex = indexRows(topRows, "latest_top10.csv");
  const screenIndex = indexRows(screenRows, "latest_screen.csv");
  const missing = [...topIndex.keys()].filter((ticker) => !screenIndex.has(ticker)).sort();
  if (missing.length) throw new Error(`latest_screen.csv is missing shortlisted tickers: ${missing.join(", ")}`);
  const ordered = [...topRows].sort((left, right) => number(left, "rank") - number(right, "rank"));
  const actualRanks = ordered.map((row) => Math.trunc(number(row, "rank")));
  if (actualRanks.some((rank, index) => rank !== index + 1)) {
    throw new Error("latest_top10.csv ranks must be unique and consecutive from 1.");
  }
  return ordered.map((row) => buildNote(row, screenIndex.get(row.ticker)));
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv);
    if (args.help) {
      console.log("Usage: node scripts/build_research_notes.js [--top10 PATH] [--screen PATH] [--output PATH]");
      return 0;
    }
    const topRows = loadCsv(args.top10, TOP_REQUIRED_FIELDS, "latest_top10.csv");
    const screenRows = loadCsv(args.screen, SCREEN_REQUIRED_FIELDS, "latest_screen.csv");
    const notes = buildResearchNotes(topRows, screenRows);
    writeCsvAtomic(args.output, notes, OUTPUT_FIELDS);
    console.log(`Wrote ${notes.length} research notes to ${args.output}`);
    return 0;
  } catch (error) {
    console.error(`Error: ${error.message}`);
    console.error("The existing research file was not changed.");
    return 1;
  }
}

if (isMain(import.meta.url)) process.exitCode = await main();
