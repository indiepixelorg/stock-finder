import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import * as ranking from "../scripts/rank_companies.js";
import { stringifyCsv } from "../scripts/lib/csv.js";
import { roundHalfEven } from "../scripts/lib/runtime.js";
import { temporaryDirectory } from "./helpers.js";

function screenRow(ticker, sector = "Industrials", cik = null, value = 1) {
  return {
    ticker, cik: cik ?? ticker, name: `${ticker} Company`, sector, subsector: "Test",
    valuation_price: "10", price_date: "2026-08-14", free_cash_flow_yield: String(0.05 * value),
    earnings_yield: String(0.04 * value), ev_to_operating_income: String(20 / value),
    operating_margin: String(0.1 * value), five_year_revenue_growth: String(0.03 * value),
    positive_fcf_years: "5", historical_fcf_years: "5", net_debt_to_fcf: String(5 / value),
    five_year_median_fcf: "100", five_year_median_net_income: "100", calculation_status: "ok",
    snapshot_source_url: "https://example.com/sec", price_source_url: "https://example.com/prices",
  };
}

test("quality score uses the disclosed weights", () => {
  assert.equal(ranking.calculateQualityScore({ operating_margin_score: 80, fcf_consistency_score: 100, revenue_growth_score: 60 }), 82);
});

test("quality labels use the display scale", () => {
  assert.equal(ranking.qualityLabel(8.5), "Strong");
  assert.equal(ranking.qualityLabel(7), "Good");
  assert.equal(ranking.qualityLabel(5.5), "Fair");
  assert.equal(ranking.qualityLabel(5.4), "Weak");
});

test("quality display uses unbiased half-even rounding", () => {
  assert.equal(roundHalfEven(8.25, 1), 8.2);
  assert.equal(roundHalfEven(8.35, 1), 8.4);
});

test("ranking labels the rounded display score", () => {
  const ranked = ranking.rankCompanies([screenRow("A", "Industrials", null, 2)], 1);
  assert.equal(ranked[0].quality_label, ranking.qualityLabel(Number(ranked[0].quality_display_score)));
});

test("percentiles average ties and respect direction", () => {
  const rows = [{ metric: 1 }, { metric: 2 }, { metric: 2 }, { metric: 4 }];
  const high = ranking.percentileScores(rows, "metric", true);
  const low = ranking.percentileScores(rows, "metric", false);
  assert.equal(high.get(0), 0);
  assert.equal(high.get(1), 50);
  assert.equal(high.get(2), 50);
  assert.equal(high.get(3), 100);
  assert.equal(low.get(0), 100);
  assert.equal(low.get(3), 0);
});

test("eligibility excludes incomplete, specialized, and unstable rows", () => {
  const partial = screenRow("PART"); partial.calculation_status = "partial";
  const financial = screenRow("BANK", "Financials");
  const unstable = screenRow("FCF"); unstable.positive_fcf_years = "3";
  const negative = screenRow("NEG"); negative.five_year_median_fcf = "-1";
  assert.equal(ranking.isEligible(partial), false);
  assert.equal(ranking.isEligible(financial), false);
  assert.equal(ranking.isEligible(unstable), false);
  assert.equal(ranking.isEligible(negative), false);
  assert.equal(ranking.isEligible(screenRow("GOOD")), true);
});

test("sector scores fall back to global scores for small groups", () => {
  const rows = Array.from({ length: 5 }, (_, index) => screenRow(`I${index}`, "Industrials", null, index + 1));
  rows.push(screenRow("U1", "Utilities", null, 0.5));
  const prepared = rows.map((source) => {
    const row = { ...source };
    for (const metric of Object.keys(ranking.METRICS)) if (metric !== "fcf_consistency") row[metric] = Number(source[metric]);
    row.positive_fcf_years = 5;
    row.historical_fcf_years = 5;
    return row;
  });
  ranking.addMetricScores(prepared);
  const globalScores = ranking.percentileScores(prepared, "free_cash_flow_yield", true);
  assert.equal(prepared[5].free_cash_flow_yield_score, globalScores.get(5));
  assert.equal(prepared[0].free_cash_flow_yield_score, 0);
  assert.equal(prepared[4].free_cash_flow_yield_score, 100);
});

test("ranking deduplicates CIK and caps sectors at two", () => {
  const rows = [screenRow("A", "Industrials", "1", 10), screenRow("A2", "Industrials", "1", 9)];
  rows.push(screenRow("B", "Industrials", null, 8), screenRow("C", "Industrials", null, 7));
  rows.push(screenRow("D", "Technology", null, 6), screenRow("E", "Health Care", null, 5));
  const ranked = ranking.rankCompanies(rows, 4);
  assert.deepEqual(ranked.map((row) => row.ticker), ["A", "B", "D", "E"]);
  assert.equal(new Set(ranked.map((row) => row.cik)).size, 4);
  for (const sector of new Set(ranked.map((row) => row.sector))) {
    assert.ok(ranked.filter((row) => row.sector === sector).length <= 2);
  }
  assert.ok(ranked.every((row) => Number(row.attractiveness_score) >= 0 && Number(row.attractiveness_score) <= 100));
  assert.ok(ranked.every((row) => Number(row.quality_score) >= 0 && Number(row.quality_score) <= 100));
});

test("extreme yields are flagged without exclusion", () => {
  const row = { ...screenRow("OUTLIER"), free_cash_flow_yield: 0.30, earnings_yield: 0.26 };
  assert.equal(ranking.reviewFlags(row), "extreme_fcf_yield_verify_inputs;extreme_earnings_yield_verify_inputs");
});

test("output cleans the source name delimiter", () => {
  const row = screenRow("A", "Industrials", null, 2); row.name = "Example Corp|";
  assert.equal(ranking.rankCompanies([row], 1)[0].name, "Example Corp");
});

test("invalid input preserves the existing output", async (context) => {
  const temporary = temporaryDirectory(); context.after(temporary.cleanup);
  const input = join(temporary.path, "screen.csv");
  const output = join(temporary.path, "top10.csv");
  writeFileSync(input, "ticker\nTEST\n");
  writeFileSync(output, "old output\n");
  assert.equal(await ranking.main(["--input", input, "--output", output]), 1);
  assert.equal(readFileSync(output, "utf8"), "old output\n");
});
