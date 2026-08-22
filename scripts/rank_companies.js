#!/usr/bin/env node

import { parseArgs as parseNodeArgs } from "node:util";
import { resolve } from "node:path";
import { readCsv, writeCsvAtomic } from "./lib/csv.js";
import { finiteNumber, isMain, projectDirectory, roundHalfEven } from "./lib/runtime.js";

export const EXCLUDED_SECTORS = new Set(["Financials", "Real Estate"]);
export const MIN_POSITIVE_FCF_YEARS = 4;
export const MIN_SECTOR_SAMPLE = 5;
export const MAX_PER_SECTOR = 2;

export const QUALITY_WEIGHTS = {
  operating_margin_score: 0.40,
  fcf_consistency_score: 0.35,
  revenue_growth_score: 0.25,
};

export const METRICS = {
  free_cash_flow_yield: [0.25, true, true],
  earnings_yield: [0.20, true, true],
  ev_to_operating_income: [0.15, false, true],
  operating_margin: [0.10, true, true],
  five_year_revenue_growth: [0.10, true, false],
  fcf_consistency: [0.10, true, false],
  net_debt_to_fcf: [0.10, false, false],
};

export const REQUIRED_FIELDS = new Set([
  "ticker", "cik", "name", "sector", "subsector", "valuation_price", "price_date",
  "free_cash_flow_yield", "earnings_yield", "ev_to_operating_income", "operating_margin",
  "five_year_revenue_growth", "positive_fcf_years", "historical_fcf_years",
  "net_debt_to_fcf", "five_year_median_fcf", "five_year_median_net_income",
  "calculation_status", "snapshot_source_url", "price_source_url",
]);

export const OUTPUT_FIELDS = [
  "rank", "ticker", "cik", "name", "sector", "subsector", "valuation_price", "price_date",
  "attractiveness_score", "quality_score", "quality_display_score", "quality_label",
  "free_cash_flow_yield", "free_cash_flow_yield_score", "earnings_yield",
  "earnings_yield_score", "ev_to_operating_income", "ev_to_operating_income_score",
  "operating_margin", "operating_margin_score", "five_year_revenue_growth",
  "revenue_growth_score", "positive_fcf_years", "historical_fcf_years",
  "fcf_consistency_score", "net_debt_to_fcf", "net_debt_score", "selection_reasons",
  "review_flags", "snapshot_source_url", "price_source_url",
];

export const SCORE_FIELDS = {
  free_cash_flow_yield: "free_cash_flow_yield_score",
  earnings_yield: "earnings_yield_score",
  ev_to_operating_income: "ev_to_operating_income_score",
  operating_margin: "operating_margin_score",
  five_year_revenue_growth: "revenue_growth_score",
  fcf_consistency: "fcf_consistency_score",
  net_debt_to_fcf: "net_debt_score",
};

export function parseArgs(argv = process.argv.slice(2)) {
  const root = projectDirectory(import.meta.url);
  const { values } = parseNodeArgs({
    args: argv,
    options: {
      input: { type: "string", default: `${root}/generated/data/latest_screen.csv` },
      output: { type: "string", default: `${root}/generated/data/latest_top10.csv` },
      limit: { type: "string", default: "10" },
      help: { type: "boolean", short: "h" },
    },
  });
  const limit = Number(values.limit);
  if (!Number.isInteger(limit)) throw new Error("--limit must be an integer.");
  return {
    input: resolve(values.input), output: resolve(values.output), limit, help: values.help ?? false,
  };
}

export function asNumber(value) {
  return finiteNumber(value);
}

export function loadScreen(path) {
  const { fields, rows } = readCsv(path);
  const missing = [...REQUIRED_FIELDS].filter((field) => !fields.includes(field)).sort();
  if (rows.length === 0 || missing.length) {
    throw new Error(`${path} is empty or missing required columns: ${missing.join(", ")}`);
  }
  return rows;
}

export function isEligible(row) {
  if (row.calculation_status !== "ok" || EXCLUDED_SECTORS.has(row.sector)) return false;
  for (const field of ["five_year_median_fcf", "five_year_median_net_income"]) {
    if ((asNumber(row[field]) ?? 0) <= 0) return false;
  }
  const positiveYears = asNumber(row.positive_fcf_years);
  const historicalYears = asNumber(row.historical_fcf_years);
  if (
    positiveYears === null || historicalYears === null
    || positiveYears < MIN_POSITIVE_FCF_YEARS || historicalYears <= 0
  ) return false;
  return Object.keys(METRICS)
    .filter((field) => field !== "fcf_consistency")
    .every((field) => asNumber(row[field]) !== null);
}

export function percentileScores(rows, metric, higherIsBetter) {
  const ordered = rows
    .map((row, index) => [index, Number(row[metric])])
    .sort((left, right) => left[1] - right[1]);
  if (ordered.length === 1) return new Map([[ordered[0][0], 50]]);

  const scores = new Map();
  let position = 0;
  while (position < ordered.length) {
    let end = position + 1;
    while (end < ordered.length && ordered[end][1] === ordered[position][1]) end += 1;
    const averageRank = (position + end - 1) / 2;
    let percentile = averageRank / (ordered.length - 1) * 100;
    if (!higherIsBetter) percentile = 100 - percentile;
    for (const [index] of ordered.slice(position, end)) scores.set(index, percentile);
    position = end;
  }
  return scores;
}

export function addMetricScores(rows) {
  const sectorGroups = new Map();
  rows.forEach((row, index) => {
    if (!sectorGroups.has(String(row.sector))) sectorGroups.set(String(row.sector), []);
    sectorGroups.get(String(row.sector)).push(index);
  });

  for (const [metric, [, higherIsBetter, sectorRelative]] of Object.entries(METRICS)) {
    const scoreField = SCORE_FIELDS[metric];
    if (metric === "fcf_consistency") {
      for (const row of rows) {
        row[scoreField] = Math.min(100, row.positive_fcf_years / row.historical_fcf_years * 100);
      }
      continue;
    }
    const globalScores = percentileScores(rows, metric, higherIsBetter);
    rows.forEach((row, index) => { row[scoreField] = globalScores.get(index); });
    if (sectorRelative) {
      for (const indexes of sectorGroups.values()) {
        if (indexes.length < MIN_SECTOR_SAMPLE) continue;
        const subset = indexes.map((index) => rows[index]);
        const localScores = percentileScores(subset, metric, higherIsBetter);
        indexes.forEach((originalIndex, localIndex) => {
          rows[originalIndex][scoreField] = localScores.get(localIndex);
        });
      }
    }
  }
}

export function formatPercent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

export function selectionReasons(row) {
  const labels = {
    free_cash_flow_yield: `FCF yield ${formatPercent(row.free_cash_flow_yield)}`,
    earnings_yield: `earnings yield ${formatPercent(row.earnings_yield)}`,
    ev_to_operating_income: `EV/operating income ${row.ev_to_operating_income.toFixed(1)}x`,
    operating_margin: `operating margin ${formatPercent(row.operating_margin)}`,
    five_year_revenue_growth: `5-year revenue growth ${formatPercent(row.five_year_revenue_growth)}`,
    fcf_consistency: `positive FCF in ${Math.trunc(row.positive_fcf_years)}/${Math.trunc(row.historical_fcf_years)} years`,
    net_debt_to_fcf: `net debt/FCF ${row.net_debt_to_fcf.toFixed(1)}x`,
  };
  return Object.entries(METRICS)
    .map(([metric, [weight]]) => ({ contribution: row[SCORE_FIELDS[metric]] * weight, metric }))
    .sort((left, right) => (
      right.contribution - left.contribution
      || (left.metric < right.metric ? 1 : left.metric > right.metric ? -1 : 0)
    ))
    .slice(0, 3)
    .map(({ metric }) => labels[metric])
    .join("; ");
}

export function reviewFlags(row) {
  const flags = [];
  if (Number(row.free_cash_flow_yield) > 0.25) flags.push("extreme_fcf_yield_verify_inputs");
  if (Number(row.earnings_yield) > 0.25) flags.push("extreme_earnings_yield_verify_inputs");
  return flags.join(";");
}

export function calculateQualityScore(row) {
  return Object.entries(QUALITY_WEIGHTS)
    .reduce((total, [field, weight]) => total + Number(row[field]) * weight, 0);
}

export function qualityLabel(displayScore) {
  if (displayScore >= 8.5) return "Strong";
  if (displayScore >= 7.0) return "Good";
  if (displayScore >= 5.5) return "Fair";
  return "Weak";
}

export function rankCompanies(rows, limit = 10) {
  if (limit <= 0) throw new Error("--limit must be greater than zero.");
  const eligible = rows.filter(isEligible).map((source) => {
    const row = { ...source };
    for (const metric of Object.keys(METRICS)) {
      if (metric !== "fcf_consistency") row[metric] = Number(source[metric]);
    }
    row.positive_fcf_years = Number(source.positive_fcf_years);
    row.historical_fcf_years = Number(source.historical_fcf_years);
    return row;
  });
  if (eligible.length === 0) throw new Error("No companies satisfy the ranking eligibility rules.");

  addMetricScores(eligible);
  for (const row of eligible) {
    row.attractiveness_score = Object.entries(METRICS)
      .reduce((total, [metric, [weight]]) => total + row[SCORE_FIELDS[metric]] * weight, 0);
    row.quality_score = calculateQualityScore(row);
    row.quality_display_score = roundHalfEven(row.quality_score / 10, 1);
    row.quality_label = qualityLabel(row.quality_display_score);
  }
  eligible.sort((left, right) => (
    right.attractiveness_score - left.attractiveness_score
    || (String(left.ticker) < String(right.ticker) ? -1 : String(left.ticker) > String(right.ticker) ? 1 : 0)
  ));

  const deduplicated = [];
  const seenCiks = new Set();
  for (const row of eligible) {
    const cik = String(row.cik);
    if (seenCiks.has(cik)) continue;
    seenCiks.add(cik);
    deduplicated.push(row);
  }
  const selected = [];
  const sectors = new Map();
  for (const row of deduplicated) {
    const sector = String(row.sector);
    if ((sectors.get(sector) ?? 0) >= MAX_PER_SECTOR) continue;
    selected.push(row);
    sectors.set(sector, (sectors.get(sector) ?? 0) + 1);
    if (selected.length === limit) break;
  }
  if (selected.length < limit) {
    throw new Error(`Only ${selected.length} companies can be selected with the sector cap; requested ${limit}.`);
  }

  return selected.map((row, index) => {
    const rendered = Object.fromEntries(OUTPUT_FIELDS.map((field) => [field, String(row[field] ?? "")]));
    rendered.rank = String(index + 1);
    rendered.name = rendered.name.replace(/\|+$/, "").trim();
    rendered.attractiveness_score = row.attractiveness_score.toFixed(2);
    rendered.quality_score = row.quality_score.toFixed(2);
    rendered.quality_display_score = row.quality_display_score.toFixed(1);
    for (const scoreField of Object.values(SCORE_FIELDS)) rendered[scoreField] = row[scoreField].toFixed(2);
    rendered.positive_fcf_years = String(Math.trunc(row.positive_fcf_years));
    rendered.historical_fcf_years = String(Math.trunc(row.historical_fcf_years));
    rendered.selection_reasons = selectionReasons(row);
    rendered.review_flags = reviewFlags(row);
    return rendered;
  });
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv);
    if (args.help) {
      console.log("Usage: node scripts/rank_companies.js [--input PATH] [--output PATH] [--limit N]");
      return 0;
    }
    const ranked = rankCompanies(loadScreen(args.input), args.limit);
    writeCsvAtomic(args.output, ranked, OUTPUT_FIELDS);
    console.log(`Wrote ${ranked.length} ranked companies to ${args.output}`);
    return 0;
  } catch (error) {
    console.error(`Error: ${error.message}`);
    return 1;
  }
}

if (isMain(import.meta.url)) process.exitCode = await main();
