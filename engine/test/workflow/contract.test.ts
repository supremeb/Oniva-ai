/**
 * Workflow node contract (plan 2026-10-17-003, U33, KTD4/KTD5/KTD16): the
 * committed schemas and fixtures under `contracts/workflow-nodes/` are
 * normative. This suite proves the engine's zod validators agree with them:
 *
 * - every fixture validates, or is rejected with the manifest's stable code;
 * - every property and enum value named in a committed schema exists in the
 *   matching zod validator (drift fails here);
 * - `limits.json` matches the engine's own bound constants;
 * - the golden previous-version checkpoint fixture decodes (U11/U12 consume it).
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import {
  CheckpointError,
  CONTRACT_VERSION,
  ContractError,
  decodeCheckpoint,
  finalizeInputSchema,
  finishInputSchema,
  MAX_BODY_BYTES,
  MAX_FANOUT_ITEMS,
  MAX_PARALLEL,
  parseFinalizeInput,
  parseFinishInput,
  parsePrepareResponse,
  prepareFanoutSchema,
  prepareFinalSchema,
  prepareLlmSchema,
  prepareResponseSchema,
  prepareWaitSchema,
} from "../../src/workflow/contract.js";

const CONTRACTS = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "contracts",
  "workflow-nodes",
);
const FIXTURES = join(CONTRACTS, "fixtures");

interface FixtureCase {
  file: string;
  schema: "prepare.response" | "finish.input" | "finalize.input";
  valid: boolean;
  code?: string;
}

const manifest = JSON.parse(readFileSync(join(FIXTURES, "manifest.json"), "utf8")) as {
  cases: FixtureCase[];
};

const VALIDATORS: Record<FixtureCase["schema"], (raw: unknown) => unknown> = {
  "prepare.response": parsePrepareResponse,
  "finish.input": parseFinishInput,
  "finalize.input": parseFinalizeInput,
};

describe("workflow node contract: fixtures", () => {
  for (const testCase of manifest.cases) {
    it(`${testCase.valid ? "accepts" : `rejects (${testCase.code})`} ${testCase.file}`, () => {
      const text = readFileSync(join(FIXTURES, testCase.file), "utf8");
      const validate = VALIDATORS[testCase.schema];
      if (testCase.valid) {
        expect(() => validate(text)).not.toThrow();
        return;
      }
      let error: unknown;
      try {
        validate(text);
      } catch (err) {
        error = err;
      }
      expect(error).toBeInstanceOf(ContractError);
      expect((error as ContractError).code).toBe(testCase.code);
    });
  }
});

function expectInvalid(fn: () => unknown): void {
  let error: unknown;
  try {
    fn();
  } catch (err) {
    error = err;
  }
  expect(error).toBeInstanceOf(ContractError);
  expect((error as ContractError).code).toBe("invalid_body");
}

describe("workflow node contract: rejection details", () => {
  it("rejects an unknown prepare kind", () => {
    expectInvalid(() => parsePrepareResponse({ contractVersion: 1, kind: "mystery" }));
  });

  it("rejects a wait with both duration fields", () => {
    expectInvalid(() =>
      parsePrepareResponse({ contractVersion: 1, kind: "wait", durationMs: 1, untilEpochMs: 1 }),
    );
  });

  it("rejects a wait with neither duration field", () => {
    expectInvalid(() => parsePrepareResponse({ contractVersion: 1, kind: "wait" }));
  });

  it("rejects a fanout over maxParallel", () => {
    expectInvalid(() =>
      parsePrepareResponse({
        contractVersion: 1,
        kind: "fanout",
        count: 1,
        parallel: MAX_PARALLEL + 1,
        failFast: false,
        target: { subgraph: "s" },
        itemsRef: "r",
      }),
    );
  });

  it("rejects a body over the size cap", () => {
    const raw = {
      contractVersion: 1,
      kind: "wait",
      durationMs: 1,
      signal: "x".repeat(MAX_BODY_BYTES),
    };
    let error: unknown;
    try {
      parsePrepareResponse(raw);
    } catch (err) {
      error = err;
    }
    expect(error).toBeInstanceOf(ContractError);
    expect((error as ContractError).code).toBe("body_too_large");
  });
});

// ---------------------------------------------------------------------------

interface Surface {
  properties: Set<string>;
  values: Set<string>;
}

/** Every property name and enum/const value anywhere in a JSON Schema. */
function surface(
  node: unknown,
  acc: Surface = { properties: new Set(), values: new Set() },
): Surface {
  if (Array.isArray(node)) {
    for (const item of node) surface(item, acc);
    return acc;
  }
  if (node === null || typeof node !== "object") return acc;
  for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
    if (
      key === "properties" &&
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value)
    ) {
      for (const [name, sub] of Object.entries(value as Record<string, unknown>)) {
        acc.properties.add(name);
        surface(sub, acc);
      }
      continue;
    }
    if (key === "enum" && Array.isArray(value)) {
      for (const v of value) acc.values.add(JSON.stringify(v));
      continue;
    }
    if (key === "const") {
      acc.values.add(JSON.stringify(value));
      continue;
    }
    surface(value, acc);
  }
  return acc;
}

/** Load a committed schema, resolving sibling-file `$ref`s. */
function loadSchema(name: string, path: string[] = []): unknown {
  if (path.includes(name)) throw new Error(`cyclic $ref: ${[...path, name].join(" -> ")}`);
  const raw = JSON.parse(readFileSync(join(CONTRACTS, name), "utf8")) as unknown;
  return resolveRefs(raw, [...path, name]);
}

function resolveRefs(node: unknown, path: string[]): unknown {
  if (Array.isArray(node)) return node.map((item) => resolveRefs(item, path));
  if (node !== null && typeof node === "object") {
    const record = node as Record<string, unknown>;
    if (typeof record.$ref === "string") {
      const file = record.$ref.split("#")[0] ?? "";
      if (file !== "") return loadSchema(file, path);
    }
    return Object.fromEntries(Object.entries(record).map(([k, v]) => [k, resolveRefs(v, path)]));
  }
  return node;
}

const SCHEMAS: Record<string, z.ZodType> = {
  "prepare.final.schema.json": prepareFinalSchema,
  "prepare.llm.schema.json": prepareLlmSchema,
  "prepare.wait.schema.json": prepareWaitSchema,
  "prepare.fanout.schema.json": prepareFanoutSchema,
  "prepare.response.schema.json": prepareResponseSchema,
  "finish.input.schema.json": finishInputSchema,
  "finalize.input.schema.json": finalizeInputSchema,
};

describe("workflow node contract: schema parity", () => {
  for (const [file, schema] of Object.entries(SCHEMAS)) {
    it(`${file} matches its zod validator`, () => {
      const committed = surface(loadSchema(file));
      const generated = surface(z.toJSONSchema(schema, { unrepresentable: "any" }));
      expect([...generated.properties].sort()).toEqual([...committed.properties].sort());
      expect([...generated.values].sort()).toEqual([...committed.values].sort());
    });
  }

  it("limits.json matches the engine's bound constants", () => {
    const limits = JSON.parse(readFileSync(join(CONTRACTS, "limits.json"), "utf8")) as {
      maxParallel: number;
      maxFanoutItems: number;
    };
    expect(limits.maxParallel).toBe(MAX_PARALLEL);
    expect(limits.maxFanoutItems).toBe(MAX_FANOUT_ITEMS);
  });

  it("uses contract version 1", () => {
    expect(CONTRACT_VERSION).toBe(1);
  });
});

describe("workflow node contract: checkpoints", () => {
  it("decodes the golden previous-version checkpoint fixture", () => {
    const raw = JSON.parse(
      readFileSync(join(FIXTURES, "checkpoint.previous-version.json"), "utf8"),
    );
    const decoded = decodeCheckpoint<{ version: number; runId: string; migrated: boolean }>(raw, {
      upgrade: (previous) => ({ ...(previous as Record<string, unknown>), migrated: true }),
    });
    expect(decoded.version).toBe(1);
    expect(decoded.runId).toBe("run-1");
    expect(decoded.migrated).toBe(true);
  });

  it("rejects a checkpoint from a newer worker", () => {
    let error: unknown;
    try {
      decodeCheckpoint({ version: 2 });
    } catch (err) {
      error = err;
    }
    expect(error).toBeInstanceOf(CheckpointError);
    expect((error as CheckpointError).code).toBe("unsupported_checkpoint_version");
  });
});
