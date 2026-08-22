#!/usr/bin/env node

import { parseArgs as parseNodeArgs } from "node:util";
import { resolve } from "node:path";
import { parseCsvObjects, writeCsvAtomic } from "./lib/csv.js";
import { isMain, projectDirectory, sleep } from "./lib/runtime.js";

export const MINIMUM_CONSTITUENTS = 450;
export const DEFAULT_CONSTITUENTS_URL =
  "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv";
export const OUTPUT_FIELDS = ["ticker", "cik", "name", "sector", "subsector"];

export function value(row, ...keys) {
  for (const key of keys) {
    const item = row[key];
    if (item !== undefined && item !== null && String(item).trim()) return String(item).trim();
  }
  return "";
}

export function normalizeTicker(ticker) {
  return ticker.trim().replaceAll(".", "-");
}

export function normalizeCik(cik) {
  const digits = String(cik ?? "").replace(/\D/g, "");
  return digits ? digits.padStart(10, "0") : "";
}

export async function fetchSource(url, { fetcher = fetch, sleeper = sleep } = {}) {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await fetcher(url, {
        headers: {
          "User-Agent": "ValueStockWeekly/0.1 universe updater",
          Accept: "text/csv,*/*;q=0.8",
        },
        signal: AbortSignal.timeout(30_000),
      });
      if (!response.ok) {
        throw new Error(`constituents.csv returned HTTP ${response.status} (${response.statusText}).`);
      }
      return await response.text();
    } catch (error) {
      lastError = error;
      if (attempt < 3) await sleeper(2_000);
    }
  }
  throw new Error(lastError?.message ?? "Could not download constituents.csv.");
}

export function loadRecords(payload) {
  const { rows } = parseCsvObjects(payload);
  const records = rows
    .map((row) => ({
      ticker: normalizeTicker(value(row, "Symbol")),
      cik: normalizeCik(value(row, "CIK")),
      name: value(row, "Security"),
      sector: value(row, "GICS Sector"),
      subsector: value(row, "GICS Sub-Industry"),
    }))
    .filter((record) => record.ticker && record.name)
    .sort((left, right) => left.ticker.localeCompare(right.ticker));

  if (records.length < MINIMUM_CONSTITUENTS) {
    throw new Error(`Expected at least ${MINIMUM_CONSTITUENTS} constituents, received ${records.length}.`);
  }
  const duplicateTickers = records.length - new Set(records.map((record) => record.ticker)).size;
  if (duplicateTickers) throw new Error(`The source contains ${duplicateTickers} duplicate tickers.`);
  return records;
}

export function parseArgs(argv = process.argv.slice(2)) {
  const { positionals, values } = parseNodeArgs({
    args: argv,
    allowPositionals: true,
    options: { help: { type: "boolean", short: "h" } },
  });
  if (positionals.length > 1) throw new Error("Expected at most one output path.");
  return {
    help: values.help ?? false,
    output: resolve(positionals[0] ?? `${projectDirectory(import.meta.url)}/generated/universe.csv`),
  };
}

export async function main(argv = process.argv.slice(2)) {
  let args;
  try {
    args = parseArgs(argv);
    if (args.help) {
      console.log("Usage: node scripts/update_universe.js [output]");
      return 0;
    }
    const payload = await fetchSource(DEFAULT_CONSTITUENTS_URL);
    const records = loadRecords(payload);
    writeCsvAtomic(args.output, records, OUTPUT_FIELDS);
    console.log(`Wrote ${records.length} constituents to ${args.output} from constituents.csv`);
    return 0;
  } catch (error) {
    console.error(`Error: ${error.message}`);
    console.error("The existing universe file was not changed.");
    return 1;
  }
}

if (isMain(import.meta.url)) process.exitCode = await main();
