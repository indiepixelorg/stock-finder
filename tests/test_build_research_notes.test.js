import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import * as research from "../scripts/build_research_notes.js";
import { stringifyCsv } from "../scripts/lib/csv.js";
import { temporaryDirectory } from "./helpers.js";

function topRow(ticker = "TEST") {
  return {
    rank: "1", ticker, cik: "0000000001", name: "Test Company", sector: "Information Technology",
    subsector: "Software", valuation_price: "100", price_date: "2026-08-14",
    attractiveness_score: "88.25", quality_score: "87.50", quality_display_score: "8.8",
    quality_label: "Strong", free_cash_flow_yield: "0.09", free_cash_flow_yield_score: "90",
    earnings_yield: "0.06", earnings_yield_score: "85", ev_to_operating_income: "12.5",
    operating_margin: "0.35", operating_margin_score: "90", five_year_revenue_growth: "0.10",
    positive_fcf_years: "5", historical_fcf_years: "5", net_debt_to_fcf: "-0.5",
    selection_reasons: "FCF yield 9.0%; earnings yield 6.0%; margin 35.0%", review_flags: "",
    snapshot_source_url: "https://example.com/sec", price_source_url: "https://example.com/prices",
  };
}

function screenRow(ticker = "TEST") {
  return {
    ticker, cik: "0000000001", filing_date: "2026-08-10", period_end: "2026-06-30",
    net_income_ttm: "120", free_cash_flow_ttm: "150", total_debt: "50", stockholders_equity: "100",
    five_year_median_fcf: "100", five_year_median_net_income: "100",
  };
}

test("builds a traceable positive summary", () => {
  const note = research.buildNote(topRow(), screenRow());
  assert.match(note.why_selected, /88\.25\/100/);
  assert.match(note.valuation_summary, /9\.0% free-cash-flow yield/);
  assert.match(note.business_quality_summary, /Operating margin is 35\.0%/);
  assert.match(note.business_quality_summary, /8\.8\/10 \(Strong\)/);
  assert.match(note.business_quality_summary, /positive in 5 of 5/);
  assert.match(note.growth_summary, /10\.0% annually/);
  assert.match(note.balance_sheet_summary, /more cash than debt/);
  assert.match(note.balance_sheet_summary, /net cash equal to 0\.5x/);
  assert.equal(note.review_status, "standard_review");
  assert.equal(note.generation_method, "deterministic_rules_v1");
  assert.equal(note.quality_score, "87.50");
  assert.equal(note.quality_display_score, "8.8");
  assert.equal(note.quality_label, "Strong");
});

test("flags extreme and non-recurring-looking results", () => {
  const top = topRow(); top.review_flags = "extreme_fcf_yield_verify_inputs";
  const screen = screenRow(); screen.net_income_ttm = "300"; screen.free_cash_flow_ttm = "250";
  const note = research.buildNote(top, screen);
  assert.equal(note.review_status, "priority_review");
  assert.match(note.warning_summary, /share count/);
  assert.match(note.warning_summary, /TTM net income is 3\.0x/);
  assert.match(note.warning_summary, /TTM free cash flow is 2\.5x/);
  assert.match(note.what_to_verify, /shares outstanding/);
});

test("adds sector-specific verification", () => {
  const note = research.buildNote(topRow(), screenRow());
  assert.match(note.what_to_verify, /product transitions/);
  assert.match(note.what_to_verify, /latest 10-K or 10-Q/);
});

test("flags suspiciously low reported debt", () => {
  const screen = screenRow(); screen.total_debt = "5";
  const note = research.buildNote(topRow(), screen);
  assert.equal(note.review_status, "priority_review");
  assert.match(note.warning_summary, /less than 10%/);
  assert.match(note.what_to_verify, /lease obligations/);
});

test("rejects a CIK mismatch", () => {
  const screen = screenRow(); screen.cik = "0000000002";
  assert.throws(() => research.buildNote(topRow(), screen), /CIK mismatch/);
});

test("invalid input preserves the existing output", async (context) => {
  const temporary = temporaryDirectory(); context.after(temporary.cleanup);
  const topPath = join(temporary.path, "top.csv");
  const screenPath = join(temporary.path, "screen.csv");
  const outputPath = join(temporary.path, "research.csv");
  writeFileSync(topPath, "ticker\nTEST\n");
  const screen = screenRow();
  writeFileSync(screenPath, stringifyCsv([screen], Object.keys(screen)));
  writeFileSync(outputPath, "old output\n");
  assert.equal(await research.main(["--top10", topPath, "--screen", screenPath, "--output", outputPath]), 1);
  assert.equal(readFileSync(outputPath, "utf8"), "old output\n");
});
