import assert from "node:assert/strict";
import test from "node:test";
import { basename } from "node:path";
import * as pipeline from "../scripts/run_pipeline.js";
import { temporaryDirectory } from "./helpers.js";

test("requires the HF API key before running any stage", async () => {
  let called = false;
  const result = await pipeline.runPipeline({ environment: {}, runner: async () => { called = true; return 0; } });
  assert.equal(result, 1);
  assert.equal(called, false);
});

test("runs every stage in order with the current environment", async (context) => {
  const temporary = temporaryDirectory(); context.after(temporary.cleanup);
  const calls = [];
  const result = await pipeline.runPipeline({
    environment: { HF_DATA_API_KEY: "test-key" },
    node: "test-node",
    projectDir: temporary.path,
    runner: async (command, options) => { calls.push([command, options]); return 0; },
  });
  assert.equal(result, 0);
  assert.deepEqual(calls.map(([command]) => basename(command[1])), pipeline.PIPELINE_STEPS.map(([, script]) => script));
  assert.ok(calls.every(([command]) => command[0] === "test-node"));
  assert.ok(calls.every(([, options]) => options.env.HF_DATA_API_KEY === "test-key"));
});

test("stops at the first failed stage", async () => {
  const calls = [];
  const result = await pipeline.runPipeline({
    environment: { HF_DATA_API_KEY: "test-key" },
    runner: async (command) => {
      const script = basename(command[1]); calls.push(script);
      return script === "update_prices.js" ? 7 : 0;
    },
  });
  assert.equal(result, 7);
  assert.deepEqual(calls, ["update_universe.js", "update_snapshot.js", "update_prices.js"]);
});
