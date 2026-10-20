/**
 * The graphs the backend compiler emits (phase 6 U15) are accepted by the real engine validator.
 *
 * The fixtures are written by `backend/tests/internal/test_workflow_compiler_engine.py` (golden files,
 * regenerated with `UPDATE_WORKFLOW_GRAPHS=1`); here every one goes through `parseRunInput`, the same
 * check `POST /v1/runs` applies. A compiler change the engine would refuse fails one of the two suites.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseRunInput } from "../../src/workflow/run-task.js";

const DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../backend/tests/fixtures/workflow_graphs",
);
const FILES = readdirSync(DIR).filter((f) => f.endsWith(".json"));

describe("backend-compiled graphs", () => {
  it("covers the representative workflows", () => {
    expect(FILES.sort()).toEqual([
      "branch.json",
      "iteration.json",
      "join.json",
      "linear.json",
      "subworkflow.json",
    ]);
  });

  for (const file of FILES) {
    it(`${file} passes the engine run-input validation`, () => {
      const raw = JSON.parse(readFileSync(join(DIR, file), "utf8")) as unknown;
      const input = parseRunInput(raw);
      expect(input.graph.nodes.length).toBeGreaterThan(0);
    });
  }

  it("a fan-out node is marked mayFanOut so the run learns it parked", () => {
    const iteration = parseRunInput(
      JSON.parse(readFileSync(join(DIR, "iteration.json"), "utf8")) as unknown,
    );
    expect(iteration.graph.nodes.find((n) => n.id === "it")?.mayFanOut).toBe(true);
    expect(Object.keys(iteration.graph.subgraphs ?? {})).toEqual(["it:sub"]);
    const sub = parseRunInput(
      JSON.parse(readFileSync(join(DIR, "subworkflow.json"), "utf8")) as unknown,
    );
    expect(sub.graph.nodes.find((n) => n.id === "sub")?.mayFanOut).toBe(true);
    expect(Object.keys(sub.graph.workflows ?? {})).toHaveLength(1);
  });
});
