import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { parquetWriteBuffer } from "hyparquet-writer";
import * as prices from "../scripts/update_prices.js";
import { addDays, todayLocal } from "../scripts/lib/dates.js";
import { parseCsvObjects } from "../scripts/lib/csv.js";
import { temporaryDirectory } from "./helpers.js";

function parquetBytes(columns) {
  return parquetWriteBuffer({
    columnData: Object.entries(columns).map(([name, data]) => ({
      name,
      data,
      type: data.every((value) => typeof value === "number") ? "DOUBLE" : "STRING",
    })),
  });
}

function dailyParquet(values) {
  const dates = values.map(([date]) => date);
  const closes = values.map(([, close]) => close);
  return parquetBytes({
    Date: dates, Open: closes, High: closes, Low: closes, Close: closes,
    Volume: values.map(() => 100),
  });
}

test("selects the median of five recent closes", async () => {
  const today = "2026-08-17";
  const payload = dailyParquet([
    [addDays(today, -7), 90], [addDays(today, -6), 100], [addDays(today, -5), 104],
    [addDays(today, -4), 102], [addDays(today, -3), 101], [addDays(today, -2), 99],
  ]);
  assert.deepEqual(await prices.selectPrice(payload, today), [101, addDays(today, -2), 5, ""]);
});

test("marks a stale latest price", async () => {
  const today = "2026-08-17";
  assert.deepEqual(
    await prices.selectPrice(dailyParquet([[addDays(today, -8), 100]]), today),
    [null, addDays(today, -8), 0, "stale_price"],
  );
});

test("requires three recent prices", async () => {
  const today = "2026-08-17";
  assert.deepEqual(
    await prices.selectPrice(dailyParquet([[addDays(today, -3), 100], [addDays(today, -1), 102]]), today),
    [null, addDays(today, -1), 2, "insufficient_recent_prices"],
  );
});

test("rejects missing columns and invalid prices", async () => {
  const today = "2026-08-17";
  const missingClose = parquetBytes({ Date: ["2026-08-14"], Open: [100] });
  const invalidClose = parquetBytes({ Date: ["2026-08-14"], Close: [-1] });
  for (const payload of [missingClose, invalidClose]) {
    assert.deepEqual(await prices.selectPrice(payload, today), [null, null, 0, "invalid_price_data"]);
  }
});

test("HF client requests the Parquet download format", async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push([url, options]);
    if (url.endsWith("/symbols")) {
      return new Response(JSON.stringify({ symbols: [{ ticker: "AAPL" }] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/download-token/")) {
      return new Response(JSON.stringify({ url: "https://download.test/AAPL.parquet" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(dailyParquet([["2026-08-14", 100]]), { status: 200 });
  };
  const client = new prices.HFClient({ apiKey: "test-key", fetcher, sleeper: async () => {} });
  assert.deepEqual(await client.getSymbols(), new Set(["AAPL"]));
  await client.downloadDailyParquet("AAPL");
  const tokenCall = calls.find(([url]) => url.includes("/download-token/"));
  const tokenUrl = new URL(tokenCall[0]);
  assert.equal(tokenUrl.searchParams.get("format"), "parquet");
  assert.equal(tokenCall[1].headers["X-API-Key"], "test-key");
  assert.equal(calls.at(-1)[1].headers["X-API-Key"], undefined);
});

test("writes all universe rows in order with exclusion reasons", async (context) => {
  const today = todayLocal();
  const priceData = new Map([
    ["AAPL", dailyParquet([
      [addDays(today, -6), 98], [addDays(today, -5), 99], [addDays(today, -4), 100],
      [addDays(today, -3), 101], [addDays(today, -2), 102],
    ])],
    ["JPM", dailyParquet([[addDays(today, -2), 200], [addDays(today, -1), 201]])],
  ]);
  const temporary = temporaryDirectory(); context.after(temporary.cleanup);
  const universe = join(temporary.path, "universe.csv");
  const output = join(temporary.path, "latest_prices.csv");
  writeFileSync(universe, "ticker,name\nAAPL,Apple Inc.\nMETA,Meta Platforms\nJPM,JPMorgan Chase\n");
  writeFileSync(output, "old snapshot\n");
  const client = {
    async getSymbols() { return new Set(priceData.keys()); },
    async downloadDailyParquet(ticker) { return priceData.get(ticker); },
  };
  assert.equal(await prices.main(
    ["--universe", universe, "--output", output, "--request-delay", "0"],
    { environment: { HF_DATA_API_KEY: "test-key" }, clientFactory: () => client, today },
  ), 0);
  const rows = parseCsvObjects(readFileSync(output, "utf8")).rows;
  assert.deepEqual(rows.map((row) => row.ticker), ["AAPL", "META", "JPM"]);
  assert.equal(rows[0].valuation_price, "100");
  assert.equal(rows[0].status, "ok");
  assert.equal(rows[1].status, "excluded");
  assert.equal(rows[1].reason, "ticker_unavailable");
  assert.equal(rows[2].status, "excluded");
  assert.equal(rows[2].reason, "insufficient_recent_prices");
  assert.equal(rows[2].observations, "2");
});

test("missing API key preserves the existing output", async (context) => {
  const temporary = temporaryDirectory(); context.after(temporary.cleanup);
  const universe = join(temporary.path, "universe.csv");
  const output = join(temporary.path, "latest_prices.csv");
  writeFileSync(universe, "ticker,name\nAAPL,Apple Inc.\n");
  writeFileSync(output, "old snapshot\n");
  assert.equal(await prices.main(
    ["--universe", universe, "--output", output],
    { environment: {} },
  ), 1);
  assert.equal(readFileSync(output, "utf8"), "old snapshot\n");
});
