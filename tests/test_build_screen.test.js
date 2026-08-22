import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import * as buildScreen from "../scripts/build_screen.js";
import { stringifyCsv } from "../scripts/lib/csv.js";
import { temporaryDirectory } from "./helpers.js";

function universeRow() {
  return { ticker: "TEST", cik: "0000000001", name: "Test Company", sector: "Industrials", subsector: "Test Equipment" };
}

function snapshotRow() {
  const row = {
    ticker: "TEST", revenue_ttm: "500", operating_income_ttm: "150", net_income_ttm: "100",
    free_cash_flow_ttm: "80", cash_and_equivalents: "100", total_debt: "300",
    stockholders_equity: "400", shares_outstanding: "100", filing_date: "2026-05-01",
    period_end: "2026-03-31", source_url: "https://example.com/sec",
  };
  const annualRevenue = [200, 170, 145, 120, 100];
  const annualFcf = [10, 0, -1, 5, 20];
  const annualNetIncome = [5, 4, 3, 2, 1];
  for (let slot = 0; slot < 5; slot += 1) {
    row[`annual_period_end_${slot}`] = `${2025 - slot}-12-31`;
    row[`revenue_fy${slot}`] = String(annualRevenue[slot]);
    row[`free_cash_flow_fy${slot}`] = String(annualFcf[slot]);
    row[`net_income_fy${slot}`] = String(annualNetIncome[slot]);
  }
  return row;
}

function priceRow(status = "ok", reason = "") {
  return {
    ticker: "TEST", valuation_price: "10", price_date: "2026-08-14", observations: "5",
    status, reason, source_url: "https://example.com/prices",
  };
}

test("calculates requested screen metrics", () => {
  const row = buildScreen.buildRow(universeRow(), snapshotRow(), priceRow());
  assert.equal(row.market_cap, "1000");
  assert.equal(row.net_debt, "200");
  assert.equal(row.enterprise_value, "1200");
  assert.equal(row.earnings_yield, "0.1");
  assert.equal(row.free_cash_flow_yield, "0.08");
  assert.equal(row.price_to_earnings, "10");
  assert.equal(row.price_to_fcf, "12.5");
  assert.equal(row.ev_to_operating_income, "8");
  assert.equal(row.operating_margin, "0.3");
  assert.ok(Math.abs(Number(row.five_year_revenue_growth) - 0.1892) < 0.001);
  assert.equal(row.positive_fcf_years, 3);
  assert.equal(row.historical_fcf_years, 5);
  assert.equal(row.net_debt_to_fcf, "2.5");
  assert.equal(row.price_to_book, "2.5");
  assert.equal(row.five_year_median_fcf, "5");
  assert.equal(row.historical_net_income_years, 5);
  assert.equal(row.five_year_median_net_income, "3");
  assert.equal(row.calculation_status, "ok");
  assert.equal(row.calculation_warnings, "");
});

test("keeps negative yields but blanks economically meaningless multiples", () => {
  const snapshot = snapshotRow();
  Object.assign(snapshot, { net_income_ttm: "-20", free_cash_flow_ttm: "-10", operating_income_ttm: "-5" });
  const row = buildScreen.buildRow(universeRow(), snapshot, priceRow());
  assert.equal(row.earnings_yield, "-0.02");
  assert.equal(row.free_cash_flow_yield, "-0.01");
  assert.equal(row.operating_margin, "-0.01");
  assert.equal(row.price_to_earnings, "");
  assert.equal(row.price_to_fcf, "");
  assert.equal(row.ev_to_operating_income, "");
  assert.equal(row.net_debt_to_fcf, "");
  assert.equal(row.calculation_status, "partial");
  assert.match(row.calculation_warnings, /nonpositive_net_income_ttm/);
  assert.match(row.calculation_warnings, /nonpositive_free_cash_flow_ttm/);
});

test("does not treat missing balance-sheet values as zero", () => {
  const snapshot = snapshotRow();
  snapshot.total_debt = "";
  const row = buildScreen.buildRow(universeRow(), snapshot, priceRow());
  assert.equal(row.market_cap, "1000");
  assert.equal(row.net_debt, "");
  assert.equal(row.enterprise_value, "");
  assert.equal(row.ev_to_operating_income, "");
  assert.equal(row.calculation_status, "partial");
  assert.match(row.calculation_warnings, /missing_total_debt/);
});

test("keeps companies with excluded prices but does not calculate metrics", () => {
  const row = buildScreen.buildRow(universeRow(), snapshotRow(), priceRow("excluded", "ticker_unavailable"));
  assert.equal(row.ticker, "TEST");
  assert.equal(row.market_cap, "");
  assert.equal(row.calculation_status, "excluded");
  assert.equal(row.calculation_warnings, "ticker_unavailable");
});

test("mismatched inputs preserve the existing output", async (context) => {
  const temporary = temporaryDirectory();
  context.after(temporary.cleanup);
  const universePath = join(temporary.path, "universe.csv");
  const snapshotPath = join(temporary.path, "snapshot.csv");
  const pricesPath = join(temporary.path, "prices.csv");
  const outputPath = join(temporary.path, "screen.csv");
  writeFileSync(universePath, stringifyCsv([universeRow()], Object.keys(universeRow())));
  const snapshot = snapshotRow();
  snapshot.ticker = "OTHER";
  writeFileSync(snapshotPath, stringifyCsv([snapshot], Object.keys(snapshot)));
  writeFileSync(pricesPath, stringifyCsv([priceRow()], Object.keys(priceRow())));
  writeFileSync(outputPath, "old output\n");
  const result = await buildScreen.main([
    "--universe", universePath, "--snapshot", snapshotPath, "--prices", pricesPath, "--output", outputPath,
  ]);
  assert.equal(result, 1);
  assert.equal(readFileSync(outputPath, "utf8"), "old output\n");
});
