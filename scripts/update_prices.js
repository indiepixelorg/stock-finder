#!/usr/bin/env node

import { parseArgs as parseNodeArgs } from "node:util";
import { resolve } from "node:path";
import { parquetMetadataAsync, parquetReadObjects, parquetSchema } from "hyparquet";
import { compressors } from "hyparquet-compressors";
import { readCsv, writeCsvAtomic } from "./lib/csv.js";
import { addDays, daysBetween, parseDate, todayLocal } from "./lib/dates.js";
import { finiteNumber, isMain, projectDirectory, sleep } from "./lib/runtime.js";

export const DEFAULT_BASE_URL = "https://api.hfdatalibrary.com/v1";
export const DEFAULT_USER_AGENT = "ValueStockWeekly/0.1 (contact: indiepixelorg@outlook.com)";
export const DEFAULT_TIMEOUT_MILLISECONDS = 60_000;
export const DEFAULT_RETRIES = 3;
export const DEFAULT_REQUEST_DELAY_SECONDS = 0.65;
export const LOOKBACK_CALENDAR_DAYS = 10;
export const MAX_PRICE_AGE_DAYS = 7;
export const MIN_OBSERVATIONS = 3;
export const MAX_OBSERVATIONS = 5;
export const DATA_SOURCE = "HF Data Library / IEX HIST";
export const SOURCE_URL = "https://hfdatalibrary.com/pages/data";

export const OUTPUT_FIELDS = [
  "ticker", "name", "valuation_price", "price_date", "observations",
  "status", "reason", "data_source", "source_url",
];

export class HFError extends Error {
  constructor(message, statusCode = null) {
    super(message);
    this.name = "HFError";
    this.statusCode = statusCode;
  }
}

export function parseArgs(argv = process.argv.slice(2)) {
  const root = projectDirectory(import.meta.url);
  const { values } = parseNodeArgs({
    args: argv,
    options: {
      universe: { type: "string", default: `${root}/generated/universe.csv` },
      output: { type: "string", default: `${root}/generated/data/latest_prices.csv` },
      limit: { type: "string" },
      "request-delay": { type: "string", default: String(DEFAULT_REQUEST_DELAY_SECONDS) },
      help: { type: "boolean", short: "h" },
    },
  });
  const limit = values.limit === undefined ? null : Number(values.limit);
  const requestDelay = Number(values["request-delay"]);
  if (limit !== null && !Number.isInteger(limit)) throw new Error("--limit must be an integer.");
  if (!Number.isFinite(requestDelay)) throw new Error("--request-delay must be a number.");
  return {
    universe: resolve(values.universe),
    output: resolve(values.output),
    limit,
    requestDelay,
    help: values.help ?? false,
  };
}

export function loadUniverse(path, limit = null) {
  const { fields, rows } = readCsv(path);
  const required = ["ticker", "name"];
  if (rows.length === 0 || required.some((field) => !fields.includes(field))) {
    throw new Error(`Universe must contain these columns: ${required.sort().join(", ")}`);
  }
  const securities = [];
  const seen = new Set();
  for (const row of rows) {
    const ticker = String(row.ticker ?? "").trim();
    if (!ticker) continue;
    if (seen.has(ticker)) throw new Error(`Duplicate ticker in universe.csv: ${ticker}`);
    seen.add(ticker);
    securities.push({ ticker, name: String(row.name ?? "").trim() });
  }
  if (limit !== null) {
    if (limit < 1) throw new Error("--limit must be greater than zero.");
    securities.splice(limit);
  }
  if (securities.length === 0) throw new Error("No securities found in the universe.");
  return securities;
}

export function retryDelay(retryAfter, attempt, now = Date.now()) {
  if (retryAfter) {
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds)) return Math.max(0, seconds) * 1_000;
    const retryAt = Date.parse(retryAfter);
    if (Number.isFinite(retryAt)) return Math.max(0, retryAt - now);
  }
  return (2 ** attempt) * 1_000;
}

export class HFClient {
  constructor({
    apiKey,
    baseUrl = DEFAULT_BASE_URL,
    timeout = DEFAULT_TIMEOUT_MILLISECONDS,
    fetcher = fetch,
    sleeper = sleep,
  }) {
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.timeout = timeout;
    this.fetcher = fetcher;
    this.sleeper = sleeper;
  }

  async requestBytes(url, authenticated) {
    const headers = {
      "User-Agent": DEFAULT_USER_AGENT,
      Accept: "application/json,application/octet-stream,*/*;q=0.8",
    };
    if (authenticated) headers["X-API-Key"] = this.apiKey;
    let lastError;
    for (let attempt = 0; attempt < DEFAULT_RETRIES; attempt += 1) {
      try {
        const response = await this.fetcher(url, {
          headers,
          signal: AbortSignal.timeout(this.timeout),
        });
        if (response.ok) return Buffer.from(await response.arrayBuffer());

        const safeUrl = url.split("?", 1)[0];
        let detail = "";
        try {
          const body = await response.json();
          if (body && typeof body === "object" && body.error) detail = ` ${body.error}`;
        } catch {}
        const message = `HF Data Library returned HTTP ${response.status} for ${safeUrl}.${detail}`;
        if ([400, 401, 403, 404].includes(response.status)) throw new HFError(message, response.status);
        lastError = new HFError(message, response.status);
        if (attempt < DEFAULT_RETRIES - 1) {
          await this.sleeper(retryDelay(response.headers.get("retry-after"), attempt));
        }
      } catch (error) {
        if (error instanceof HFError && [400, 401, 403, 404].includes(error.statusCode)) throw error;
        lastError = error instanceof HFError
          ? error
          : new HFError(`HF Data Library request failed for ${url}: ${error.message}`);
        if (attempt < DEFAULT_RETRIES - 1) await this.sleeper((2 ** attempt) * 1_000);
      }
    }
    throw lastError ?? new HFError(`HF Data Library request failed for ${url}.`);
  }

  async requestJson(url, authenticated) {
    const payload = await this.requestBytes(url, authenticated);
    let value;
    try {
      value = JSON.parse(payload.toString("utf8"));
    } catch {
      throw new HFError(`HF Data Library returned invalid JSON for ${url}.`);
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new HFError(`HF Data Library returned an unexpected response for ${url}.`);
    }
    return value;
  }

  async getSymbols() {
    const payload = await this.requestJson(`${this.baseUrl}/symbols`, false);
    if (!Array.isArray(payload.symbols)) {
      throw new HFError("HF Data Library symbol response has no symbols list.");
    }
    const symbols = new Set(payload.symbols
      .filter((item) => item && typeof item === "object")
      .map((item) => String(item.ticker ?? "").trim())
      .filter(Boolean));
    if (symbols.size === 0) throw new HFError("HF Data Library returned an empty symbol list.");
    return symbols;
  }

  async downloadDailyParquet(ticker) {
    const query = new URLSearchParams({ timeframe: "daily", format: "parquet", version: "clean" });
    const tokenUrl = `${this.baseUrl}/download-token/${encodeURIComponent(ticker)}?${query}`;
    const payload = await this.requestJson(tokenUrl, true);
    if (typeof payload.url !== "string" || !/^https?:\/\//.test(payload.url)) {
      throw new HFError(`HF Data Library returned no download URL for ${ticker}.`);
    }
    return this.requestBytes(payload.url, false);
  }
}

export function parsePriceDate(value) {
  return parseDate(value);
}

export function parseClose(value) {
  const close = finiteNumber(value);
  return close !== null && close > 0 ? close : null;
}

export function median(values) {
  const ordered = [...values].sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

function asArrayBuffer(payload) {
  if (payload instanceof ArrayBuffer) return payload;
  if (ArrayBuffer.isView(payload)) {
    return payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength);
  }
  throw new TypeError("Price payload must be an ArrayBuffer or typed-array view.");
}

export async function selectPrice(payload, today) {
  let rows;
  let fields;
  try {
    const file = asArrayBuffer(payload);
    const metadata = await parquetMetadataAsync(file);
    fields = parquetSchema(metadata).children.map((child) => child.element.name);
    const dateField = ["Date", "date", "datetime", "Datetime", "timestamp"]
      .find((field) => fields.includes(field));
    const closeField = ["Close", "close"].find((field) => fields.includes(field));
    if (!dateField || !closeField) return [null, null, 0, "invalid_price_data"];
    rows = await parquetReadObjects({
      file,
      metadata,
      columns: [dateField, closeField],
      compressors,
    });
  } catch {
    return [null, null, 0, "invalid_price_data"];
  }
  const dateField = ["Date", "date", "datetime", "Datetime", "timestamp"]
    .find((field) => fields.includes(field));
  const closeField = ["Close", "close"].find((field) => fields.includes(field));

  const pricesByDate = new Map();
  for (const row of rows) {
    const priceDate = parsePriceDate(row[dateField]);
    const close = parseClose(row[closeField]);
    if (priceDate && priceDate <= today && close !== null) pricesByDate.set(priceDate, close);
  }
  if (pricesByDate.size === 0) return [null, null, 0, "invalid_price_data"];

  const newestDate = [...pricesByDate.keys()].sort().at(-1);
  if (daysBetween(newestDate, today) > MAX_PRICE_AGE_DAYS) {
    return [null, newestDate, 0, "stale_price"];
  }
  const windowStart = addDays(today, -LOOKBACK_CALENDAR_DAYS);
  const recent = [...pricesByDate]
    .filter(([priceDate]) => priceDate >= windowStart)
    .sort(([left], [right]) => right.localeCompare(left))
    .slice(0, MAX_OBSERVATIONS);
  if (recent.length < MIN_OBSERVATIONS) {
    return [null, newestDate, recent.length, "insufficient_recent_prices"];
  }
  return [median(recent.map(([, close]) => close)), newestDate, recent.length, ""];
}

export function formatPrice(value) {
  return value.toFixed(6).replace(/\.?0+$/, "");
}

export function excludedRow(security, reason, {
  priceDate = null, observations = 0, sourced = false,
} = {}) {
  return {
    ...security,
    valuation_price: "",
    price_date: priceDate ?? "",
    observations: observations || "",
    status: "excluded",
    reason,
    data_source: sourced ? DATA_SOURCE : "",
    source_url: sourced ? SOURCE_URL : "",
  };
}

export function pricedRow(security, valuationPrice, priceDate, observations) {
  return {
    ...security,
    valuation_price: formatPrice(valuationPrice),
    price_date: priceDate,
    observations,
    status: "ok",
    reason: "",
    data_source: DATA_SOURCE,
    source_url: SOURCE_URL,
  };
}

export async function main(argv = process.argv.slice(2), {
  environment = process.env,
  clientFactory = (options) => new HFClient(options),
  today = todayLocal(),
} = {}) {
  let args;
  try {
    args = parseArgs(argv);
  } catch (error) {
    console.error(`Error: ${error.message}`);
    return 1;
  }
  if (args.help) {
    console.log("Usage: node scripts/update_prices.js [--universe PATH] [--output PATH] [--limit N] [--request-delay SECONDS]");
    return 0;
  }
  if (args.requestDelay < 0) {
    console.error("Error: --request-delay cannot be negative.");
    return 1;
  }
  const apiKey = String(environment.HF_DATA_API_KEY ?? "").trim();
  if (!apiKey) {
    console.error("Error: HF_DATA_API_KEY is not set.");
    console.error("The existing price snapshot was not changed.");
    return 1;
  }

  try {
    const securities = loadUniverse(args.universe, args.limit);
    const client = clientFactory({
      apiKey,
      baseUrl: environment.HF_BASE_URL ?? DEFAULT_BASE_URL,
    });
    const availableSymbols = await client.getSymbols();
    const rows = [];
    console.log(`Fetching HF daily prices for ${securities.length} securities...`);
    for (let index = 0; index < securities.length; index += 1) {
      const security = securities[index];
      if (!availableSymbols.has(security.ticker)) {
        rows.push(excludedRow(security, "ticker_unavailable"));
      } else {
        const payload = await client.downloadDailyParquet(security.ticker);
        const [price, priceDate, observations, reason] = await selectPrice(payload, today);
        rows.push(price === null || priceDate === null
          ? excludedRow(security, reason || "invalid_price_data", {
            priceDate, observations, sourced: true,
          })
          : pricedRow(security, price, priceDate, observations));
        if (args.requestDelay) await sleep(args.requestDelay * 1_000);
      }
      if ((index + 1) % 25 === 0 || index + 1 === securities.length) {
        console.log(`  companies: ${index + 1}/${securities.length}`);
      }
    }
    writeCsvAtomic(args.output, rows, OUTPUT_FIELDS);
    const usable = rows.filter((row) => row.status === "ok").length;
    console.log(`Wrote ${rows.length} rows to ${args.output}`);
    console.log(`  usable prices: ${usable}`);
    for (const reason of [
      "ticker_unavailable", "stale_price", "insufficient_recent_prices", "invalid_price_data",
    ]) {
      const count = rows.filter((row) => row.reason === reason).length;
      if (count) console.log(`  ${reason}: ${count}`);
    }
    return 0;
  } catch (error) {
    console.error(`Error: ${error.message}`);
    console.error("The existing price snapshot was not changed.");
    return 1;
  }
}

if (isMain(import.meta.url)) process.exitCode = await main();
