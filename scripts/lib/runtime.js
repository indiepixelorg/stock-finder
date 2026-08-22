import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

export function projectDirectory(moduleUrl) {
  return resolve(dirname(fileURLToPath(moduleUrl)), "..");
}

export function isMain(moduleUrl) {
  return Boolean(process.argv[1]) && moduleUrl === pathToFileURL(resolve(process.argv[1])).href;
}

export function sleep(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

export function finiteNumber(value) {
  if (value === undefined || value === null || String(value).trim() === "") return null;
  const number = Number(String(value).trim());
  return Number.isFinite(number) ? number : null;
}

export function unique(values) {
  return [...new Set(values)];
}

export function roundHalfEven(value, decimalPlaces = 0) {
  const factor = 10 ** decimalPlaces;
  const scaled = value * factor;
  const lower = Math.floor(scaled);
  const fraction = scaled - lower;
  const tolerance = Number.EPSILON * Math.max(1, Math.abs(scaled)) * 2;
  if (Math.abs(fraction - 0.5) <= tolerance) {
    return (lower % 2 === 0 ? lower : lower + 1) / factor;
  }
  return Math.round(scaled) / factor;
}
