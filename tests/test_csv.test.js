import assert from "node:assert/strict";
import test from "node:test";
import { parseCsvObjects, stringifyCsv } from "../scripts/lib/csv.js";

test("CSV helpers round-trip commas, quotes, newlines, and empty values", () => {
  const rows = [{
    ticker: "TEST",
    name: 'Example, "Quoted" Company',
    note: "first line\nsecond line",
    missing: "",
  }];
  const fields = ["ticker", "name", "note", "missing"];
  assert.deepEqual(parseCsvObjects(`\uFEFF${stringifyCsv(rows, fields)}`), { fields, rows });
});
