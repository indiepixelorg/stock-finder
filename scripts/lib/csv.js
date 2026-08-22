import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { randomUUID } from "node:crypto";

export function parseCsv(source) {
  const text = source.replace(/^\uFEFF/, "");
  const records = [];
  let record = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
      } else {
        field += character;
      }
    } else if (character === '"' && field === "") {
      quoted = true;
    } else if (character === ",") {
      record.push(field);
      field = "";
    } else if (character === "\n" || character === "\r") {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      record.push(field);
      records.push(record);
      record = [];
      field = "";
    } else {
      field += character;
    }
  }

  if (quoted) throw new Error("CSV contains an unterminated quoted field.");
  if (field !== "" || record.length > 0) {
    record.push(field);
    records.push(record);
  }
  return records;
}

export function parseCsvObjects(source) {
  const records = parseCsv(source);
  if (records.length === 0) return { fields: [], rows: [] };
  const fields = records[0];
  const rows = records.slice(1).filter((record) => record.some((value) => value !== ""));
  return {
    fields,
    rows: rows.map((record) => Object.fromEntries(
      fields.map((field, index) => [field, record[index] ?? ""]),
    )),
  };
}

export function readCsv(path) {
  if (!existsSync(path)) throw new Error(`Input file does not exist: ${path}`);
  return parseCsvObjects(readFileSync(path, "utf8"));
}

function csvValue(value) {
  const text = value === undefined || value === null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function stringifyCsv(rows, fields) {
  const lines = [fields.map(csvValue).join(",")];
  for (const row of rows) {
    lines.push(fields.map((field) => csvValue(row[field])).join(","));
  }
  return `${lines.join("\n")}\n`;
}

export function atomicWrite(path, content) {
  const outputPath = resolve(path);
  const outputDirectory = dirname(outputPath);
  mkdirSync(outputDirectory, { recursive: true });
  const temporaryPath = resolve(
    outputDirectory,
    `.${basename(outputPath)}.${process.pid}.${randomUUID()}.tmp`,
  );
  let descriptor;
  try {
    descriptor = openSync(temporaryPath, "wx");
    writeFileSync(descriptor, content, "utf8");
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
    renameSync(temporaryPath, outputPath);
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
    try {
      unlinkSync(temporaryPath);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
}

export function writeCsvAtomic(path, rows, fields) {
  atomicWrite(path, stringifyCsv(rows, fields));
}
