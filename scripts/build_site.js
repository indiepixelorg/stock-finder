#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { parseArgs as parseNodeArgs } from "node:util";
import { resolve } from "node:path";
import { atomicWrite, readCsv } from "./lib/csv.js";
import { finiteNumber, isMain, projectDirectory } from "./lib/runtime.js";

export const TIME_ZONE = "Europe/Ljubljana";
export const EXPECTED_COMPANIES = 10;
export const FONT_FILES = [
  "playfair-display-latin.woff2",
  "public-sans-latin.woff2",
  "OFL.txt",
];
export const REQUIRED_FIELDS = new Set([
  "rank", "ticker", "name", "sector", "subsector", "valuation_price", "price_date",
  "attractiveness_score", "quality_display_score", "quality_label", "why_selected",
  "valuation_summary", "business_quality_summary", "growth_summary", "balance_sheet_summary",
  "warning_summary", "what_to_verify", "review_status", "snapshot_source_url", "price_source_url",
]);

export function parseArgs(argv = process.argv.slice(2)) {
  const root = projectDirectory(import.meta.url);
  const { values } = parseNodeArgs({
    args: argv,
    options: {
      input: { type: "string", default: `${root}/generated/data/latest_research.csv` },
      templates: { type: "string", default: `${root}/templates` },
      assets: { type: "string", default: `${root}/assets` },
      output: { type: "string", default: `${root}/generated/site` },
      "published-at": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });
  return {
    input: resolve(values.input), templates: resolve(values.templates), assets: resolve(values.assets),
    output: resolve(values.output), publishedAt: values["published-at"] ?? null,
    help: values.help ?? false,
  };
}

export function loadTemplate(path) {
  try {
    return readFileSync(path, "utf8");
  } catch (error) {
    throw new Error(`Cannot read template: ${path}`, { cause: error });
  }
}

export function number(row, field) {
  const value = finiteNumber(row[field]);
  if (value === null) throw new Error(`${row.ticker || "row"} has invalid ${field}.`);
  return value;
}

export function escapeHtml(value) {
  return String(value ?? "").trim()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#x27;");
}

export function safeUrl(row, field) {
  const value = String(row[field] ?? "").trim();
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${row.ticker || "row"} has invalid ${field}.`);
  }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    throw new Error(`${row.ticker || "row"} has invalid ${field}.`);
  }
  return escapeHtml(value);
}

export function loadRows(path) {
  let parsed;
  try {
    parsed = readCsv(path);
  } catch (error) {
    if (error.code === "ENOENT" || error.message.startsWith("Input file does not exist")) {
      throw new Error(`Input file does not exist: ${path}`);
    }
    throw error;
  }
  const missing = [...REQUIRED_FIELDS].filter((field) => !parsed.fields.includes(field)).sort();
  if (missing.length) throw new Error(`Input is missing required columns: ${missing.join(", ")}`);
  if (parsed.rows.length !== EXPECTED_COMPANIES) {
    throw new Error(`Expected ${EXPECTED_COMPANIES} research rows, found ${parsed.rows.length}.`);
  }

  const ranks = [];
  const tickers = new Set();
  for (const row of parsed.rows) {
    const rankText = String(row.rank ?? "").trim();
    const rank = Number(rankText);
    if (!/^[-+]?\d+$/.test(rankText) || !Number.isInteger(rank)) {
      throw new Error("Every research row must have an integer rank.");
    }
    const ticker = String(row.ticker ?? "").trim();
    if (!ticker || tickers.has(ticker)) throw new Error(`Ticker is empty or duplicated: ${ticker || "(empty)"}`);
    ranks.push(rank);
    tickers.add(ticker);
    number(row, "valuation_price");
    const attractiveness = number(row, "attractiveness_score");
    const quality = number(row, "quality_display_score");
    if (attractiveness < 0 || attractiveness > 100 || quality < 0 || quality > 10) {
      throw new Error(`${ticker} has a score outside its expected range.`);
    }
    safeUrl(row, "snapshot_source_url");
    safeUrl(row, "price_source_url");
  }
  const sortedRanks = [...ranks].sort((left, right) => left - right);
  if (sortedRanks.some((rank, index) => rank !== index + 1)) {
    throw new Error("Research ranks must be unique and consecutive from 1 to 10.");
  }
  return [...parsed.rows].sort((left, right) => Number(left.rank) - Number(right.rank));
}

function localParts(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: TIME_ZONE,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
}

function zoneOffsetMilliseconds(date, parts = localParts(date)) {
  return Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day),
    Number(parts.hour), Number(parts.minute), Number(parts.second),
  ) - Math.floor(date.valueOf() / 1_000) * 1_000;
}

function offsetText(milliseconds) {
  const sign = milliseconds >= 0 ? "+" : "-";
  const totalMinutes = Math.abs(milliseconds) / 60_000;
  return `${sign}${String(Math.floor(totalMinutes / 60)).padStart(2, "0")}:${String(totalMinutes % 60).padStart(2, "0")}`;
}

export function publicationTime(value = null) {
  let moment;
  if (value) {
    if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) {
      throw new Error("--published-at must include a timezone offset.");
    }
    moment = new Date(value);
    if (!Number.isFinite(moment.valueOf())) throw new Error("--published-at must be a valid ISO-8601 timestamp.");
  } else {
    moment = new Date();
  }
  const parts = localParts(moment);
  const offset = zoneOffsetMilliseconds(moment, parts);
  const localHourAsUtc = Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), 0, 0,
  );
  const date = new Date(localHourAsUtc - offset);
  const truncatedParts = localParts(date);
  const truncatedOffset = zoneOffsetMilliseconds(date, truncatedParts);
  const iso = `${truncatedParts.year}-${truncatedParts.month}-${truncatedParts.day}T${truncatedParts.hour}:00:00${offsetText(truncatedOffset)}`;
  const dateDisplay = new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE, weekday: "long", month: "long", day: "numeric", year: "numeric",
  }).format(date);
  const timeParts = new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE, hour: "2-digit", hourCycle: "h23", timeZoneName: "short",
  }).formatToParts(date);
  const displayHour = timeParts.find((part) => part.type === "hour")?.value ?? truncatedParts.hour;
  // Some small-ICU Node builds render this as "GMT+2". Ljubljana has used
  // CET/CEST for the dates supported by this project, so keep output stable
  // across developer machines and GitHub Actions images.
  const zoneName = truncatedOffset === 7_200_000 ? "CEST"
    : truncatedOffset === 3_600_000 ? "CET"
      : timeParts.find((part) => part.type === "timeZoneName")?.value ?? "";
  return {
    date,
    iso,
    display: `${dateDisplay.replace(/, (?=\d{4}$)/, ", ")} · ${displayHour}:00 ${zoneName}`,
    year: truncatedParts.year,
  };
}

export function attractivenessLabel(score) {
  const display = score / 10;
  if (display >= 9.0) return "Excellent";
  if (display >= 8.5) return "Strong";
  if (display >= 8.0) return "Attractive";
  if (display >= 7.0) return "Good";
  return "Fair";
}

export function scoreTone(label) {
  if (["Excellent", "Strong"].includes(label)) return "strong";
  if (["Attractive", "Good"].includes(label)) return "good";
  if (label === "Fair") return "fair";
  return "weak";
}

export function tickerSlug(value) {
  const slug = value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  if (!slug) throw new Error(`Cannot create an HTML id from ticker: ${value}`);
  return slug;
}

export function templateValues(row) {
  const attractiveness = number(row, "attractiveness_score");
  const attrLabel = attractivenessLabel(attractiveness);
  const qualityLabel = String(row.quality_label ?? "").trim();
  return {
    rank: escapeHtml(row.rank), ticker: escapeHtml(row.ticker), ticker_slug: tickerSlug(row.ticker),
    name: escapeHtml(row.name), sector: escapeHtml(row.sector),
    subsector: escapeHtml(row.subsector || row.sector),
    price: `$${number(row, "valuation_price").toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
    attractiveness_display: (attractiveness / 10).toFixed(1), attractiveness_label: attrLabel,
    attractiveness_tone: scoreTone(attrLabel), quality_display: number(row, "quality_display_score").toFixed(1),
    quality_label: escapeHtml(qualityLabel), quality_tone: scoreTone(qualityLabel),
    why_selected: escapeHtml(row.why_selected), valuation_summary: escapeHtml(row.valuation_summary),
    business_quality_summary: escapeHtml(row.business_quality_summary), growth_summary: escapeHtml(row.growth_summary),
    balance_sheet_summary: escapeHtml(row.balance_sheet_summary), warning_summary: escapeHtml(row.warning_summary),
    what_to_verify: escapeHtml(row.what_to_verify),
    review_class: row.review_status.trim() === "priority_review" ? "priority-review" : "",
    snapshot_source_url: safeUrl(row, "snapshot_source_url"), price_source_url: safeUrl(row, "price_source_url"),
  };
}

export function substitute(template, values) {
  return template.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (match, key) => {
    if (!(key in values)) throw new Error(`Missing template value: ${key}`);
    return values[key];
  });
}

export function renderSite(rows, templatesDirectory, published, styles) {
  const page = loadTemplate(resolve(templatesDirectory, "index.html"));
  const desktopRow = loadTemplate(resolve(templatesDirectory, "partials/desktop_row.html"));
  const mobileCard = loadTemplate(resolve(templatesDirectory, "partials/mobile_card.html"));
  const analysis = loadTemplate(resolve(templatesDirectory, "partials/analysis.html"));
  const desktopParts = [];
  const mobileParts = [];
  for (const row of rows) {
    const values = templateValues(row);
    values.analysis_content = substitute(analysis, values);
    desktopParts.push(substitute(desktopRow, values).trimEnd());
    mobileParts.push(substitute(mobileCard, values).trimEnd());
  }
  return substitute(page, {
    company_count: String(rows.length), desktop_rows: desktopParts.join("\n"),
    mobile_cards: mobileParts.join("\n"), published_iso: published.iso,
    published_display: published.display, publication_year: published.year, styles,
  });
}

export function build(args) {
  const rows = loadRows(args.input);
  const published = publicationTime(args.publishedAt);
  let styles;
  let script;
  const fonts = new Map();
  try {
    styles = readFileSync(resolve(args.assets, "styles.css"), "utf8");
    script = readFileSync(resolve(args.assets, "site.js"), "utf8");
    for (const file of FONT_FILES) {
      fonts.set(file, readFileSync(resolve(args.assets, "fonts", file)));
    }
  } catch (error) {
    throw new Error(`Cannot read site assets from ${args.assets}`, { cause: error });
  }
  const rendered = renderSite(rows, args.templates, published, styles);
  atomicWrite(resolve(args.output, "index.html"), rendered);
  atomicWrite(resolve(args.output, "assets/site.js"), script);
  for (const [file, contents] of fonts) {
    atomicWrite(resolve(args.output, "assets/fonts", file), contents);
  }
  console.log(`Wrote ${resolve(args.output, "index.html")} with ${rows.length} companies (published ${published.iso}).`);
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv);
    if (args.help) {
      console.log("Usage: node scripts/build_site.js [--input PATH] [--templates PATH] [--assets PATH] [--output PATH] [--published-at ISO]");
      return 0;
    }
    build(args);
    return 0;
  } catch (error) {
    console.error(`error: ${error.message}`);
    return 1;
  }
}

if (isMain(import.meta.url)) process.exitCode = await main();
