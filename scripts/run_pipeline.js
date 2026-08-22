#!/usr/bin/env node

import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { isMain } from "./lib/runtime.js";

export const PIPELINE_STEPS = [
  ["Update S&P 500 universe", "update_universe.js"],
  ["Update SEC financial snapshot", "update_snapshot.js"],
  ["Update weekly prices", "update_prices.js"],
  ["Build valuation screen", "build_screen.js"],
  ["Rank companies", "rank_companies.js"],
  ["Build research notes", "build_research_notes.js"],
  ["Build static site", "build_site.js"],
];

export function runCommand(command, { cwd, env }) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command[0], command.slice(1), { cwd, env, stdio: "inherit" });
    child.once("error", reject);
    child.once("close", (code, signal) => resolvePromise(code ?? (signal ? 1 : 0)));
  });
}

export async function runPipeline({
  environment = process.env,
  runner = runCommand,
  node = process.execPath,
  projectDir,
} = {}) {
  const env = { ...environment };
  if (!String(env.HF_DATA_API_KEY ?? "").trim()) {
    console.error(
      "Error: HF_DATA_API_KEY is not set. Export it locally or add it as a GitHub Actions repository secret.",
    );
    return 1;
  }

  const scriptsDir = dirname(fileURLToPath(import.meta.url));
  const root = projectDir ?? resolve(scriptsDir, "..");
  for (let index = 0; index < PIPELINE_STEPS.length; index += 1) {
    const [label, scriptName] = PIPELINE_STEPS[index];
    console.log(`[${index + 1}/${PIPELINE_STEPS.length}] ${label}`);
    let returnCode;
    try {
      returnCode = await runner([node, resolve(scriptsDir, scriptName)], { cwd: root, env });
    } catch (error) {
      console.error(`Error: could not start ${scriptName}: ${error.message}`);
      return 1;
    }
    if (returnCode) {
      console.error(`Error: ${scriptName} failed with exit code ${returnCode}.`);
      return returnCode;
    }
  }
  console.log(`Pipeline complete. Static site: ${resolve(root, "generated/site")}`);
  return 0;
}

export async function main(argv = process.argv.slice(2)) {
  let values;
  try {
    ({ values } = parseArgs({
      args: argv,
      options: { help: { type: "boolean", short: "h" } },
    }));
  } catch (error) {
    console.error(`Error: ${error.message}`);
    return 1;
  }
  if (values.help) {
    console.log("Usage: npm run pipeline");
    return 0;
  }
  return runPipeline();
}

if (isMain(import.meta.url)) process.exitCode = await main();
