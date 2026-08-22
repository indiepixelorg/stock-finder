import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import * as site from "../scripts/build_site.js";
import { parseCsvObjects, stringifyCsv } from "../scripts/lib/csv.js";
import { temporaryDirectory } from "./helpers.js";

const PROJECT_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function makeResearch(path, firstName = "Company 1") {
  const fields = [...site.REQUIRED_FIELDS].sort();
  const rows = Array.from({ length: 10 }, (_, index) => {
    const rank = index + 1;
    return {
      rank, ticker: `T${rank}`, name: rank === 1 ? firstName : `Company ${rank}`,
      sector: "Industrials", subsector: "Research & Testing", valuation_price: "123.456",
      price_date: "2026-08-14", attractiveness_score: rank === 1 ? "91.63" : "80.0",
      quality_display_score: "9.2", quality_label: "Strong", why_selected: "Selected by disclosed rules.",
      valuation_summary: "Valuation explanation.", business_quality_summary: "Quality explanation.",
      growth_summary: "Growth explanation.", balance_sheet_summary: "Balance sheet explanation.",
      warning_summary: "Review the numbers.", what_to_verify: "Read the filing.",
      review_status: rank === 1 ? "priority_review" : "standard_review",
      snapshot_source_url: "https://data.sec.gov/example",
      price_source_url: "https://hfdatalibrary.com/pages/data",
    };
  });
  writeFileSync(path, stringifyCsv(rows, fields));
}

function args(input, output, publishedAt) {
  return site.parseArgs([
    "--input", input, "--templates", join(PROJECT_DIR, "templates"), "--assets", join(PROJECT_DIR, "assets"),
    "--output", output, "--published-at", publishedAt,
  ]);
}

test("builds complete GitHub Pages output", (context) => {
  const temporary = temporaryDirectory(); context.after(temporary.cleanup);
  const research = join(temporary.path, "research.csv");
  const output = join(temporary.path, "docs");
  makeResearch(research);
  site.build(args(research, output, "2026-08-22T17:47:00+02:00"));
  const generated = readFileSync(join(output, "index.html"), "utf8");
  assert.match(generated, /Saturday, August 22, 2026 · 17:00 CEST/);
  assert.doesNotMatch(generated, /Price data:/);
  assert.match(generated, /9\.2 <small>Excellent<\/small>/);
  assert.match(generated, /9\.2 <small>Strong<\/small>/);
  assert.equal(generated.match(/class="company-row"/g)?.length, 10);
  assert.equal(generated.match(/class="company-card"/g)?.length, 10);
  assert.doesNotMatch(generated, />Risk</);
  assert.ok(readFileSync(join(output, "assets/styles.css"), "utf8"));
  assert.ok(readFileSync(join(output, "assets/site.js"), "utf8"));
});

test("escapes research content", (context) => {
  const temporary = temporaryDirectory(); context.after(temporary.cleanup);
  const research = join(temporary.path, "research.csv");
  const output = join(temporary.path, "docs");
  makeResearch(research, '<script>alert("x")</script>');
  site.build(args(research, output, "2026-08-22T12:00:00Z"));
  const generated = readFileSync(join(output, "index.html"), "utf8");
  assert.doesNotMatch(generated, /<script>alert\("x"\)<\/script>/);
  assert.match(generated, /&lt;script&gt;alert\(&quot;x&quot;\)&lt;\/script&gt;/);
});

test("rejects incomplete shortlist before overwriting", (context) => {
  const temporary = temporaryDirectory(); context.after(temporary.cleanup);
  const research = join(temporary.path, "research.csv");
  const output = join(temporary.path, "docs");
  makeResearch(research);
  const parsed = parseCsvObjects(readFileSync(research, "utf8"));
  writeFileSync(research, stringifyCsv(parsed.rows.slice(0, 9), parsed.fields));
  mkdirSync(output);
  writeFileSync(join(output, "index.html"), "existing");
  assert.throws(() => site.build(args(research, output, "2026-08-22T17:00:00+02:00")), /Expected 10/);
  assert.equal(readFileSync(join(output, "index.html"), "utf8"), "existing");
});
