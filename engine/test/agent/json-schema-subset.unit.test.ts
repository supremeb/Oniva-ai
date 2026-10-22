import { describe, expect, it } from "vitest";
import {
  MAX_OUTPUT_SCHEMA_BYTES,
  MAX_OUTPUT_SCHEMA_DEPTH,
  schemaProblems,
  validateAgainstSchema,
} from "../../src/agent/json-schema-subset.js";
import type { JsonObject, JsonValue } from "../../src/core/json.js";

const ok = (schema: JsonObject, value: JsonValue) =>
  expect(validateAgainstSchema(schema, value)).toEqual([]);
const bad = (schema: JsonObject, value: JsonValue, fragment: string) => {
  const v = validateAgainstSchema(schema, value);
  expect(v.length).toBeGreaterThan(0);
  expect(v.join("\n")).toContain(fragment);
};

describe("json-schema-subset", () => {
  it("type: each type passes and fails", () => {
    ok({ type: "string" }, "a");
    bad({ type: "string" }, 1, "expected string");
    ok({ type: "number" }, 1.5);
    ok({ type: "number" }, 2);
    bad({ type: "number" }, "1", "expected number");
    ok({ type: "integer" }, 2);
    bad({ type: "integer" }, 1.5, "expected integer");
    ok({ type: "boolean" }, false);
    bad({ type: "boolean" }, 0, "expected boolean");
    ok({ type: "null" }, null);
    bad({ type: "null" }, "x", "expected null");
    ok({ type: "object" }, {});
    bad({ type: "object" }, [], "expected object");
    ok({ type: "array" }, []);
    bad({ type: "array" }, {}, "expected array");
  });

  it("properties and required, nested paths in messages", () => {
    const s: JsonObject = {
      type: "object",
      required: ["a"],
      properties: {
        a: { type: "string" },
        b: { type: "object", properties: { c: { type: "integer" } } },
      },
    };
    ok(s, { a: "x", extra: 1 });
    bad(s, {}, 'missing required property "a"');
    bad(s, { a: 1 }, "$.a: expected string");
    bad(s, { a: "x", b: { c: "no" } }, "$.b.c: expected integer");
  });

  it("items, minItems, maxItems", () => {
    const s: JsonObject = { type: "array", items: { type: "string" }, minItems: 1, maxItems: 2 };
    ok(s, ["a"]);
    bad(s, [], "fewer than 1");
    bad(s, ["a", "b", "c"], "more than 2");
    bad(s, ["a", 1], "$[1]: expected string");
  });

  it("enum", () => {
    const s: JsonObject = { enum: ["a", 2, { k: 1 }] };
    ok(s, "a");
    ok(s, { k: 1 });
    bad(s, "b", "must be one of");
  });

  it("minLength, maxLength", () => {
    const s: JsonObject = { type: "string", minLength: 2, maxLength: 3 };
    ok(s, "ab");
    bad(s, "a", "shorter than 2");
    bad(s, "abcd", "longer than 3");
  });

  it("minimum, maximum", () => {
    const s: JsonObject = { type: "number", minimum: 0, maximum: 10 };
    ok(s, 0);
    ok(s, 10);
    bad(s, -1, "below 0");
    bad(s, 11, "above 10");
  });

  it("collects several violations", () => {
    const s: JsonObject = {
      type: "object",
      required: ["a", "b"],
      properties: { c: { type: "string" } },
    };
    expect(validateAgainstSchema(s, { c: 1 }).length).toBe(3);
  });

  describe("schemaProblems", () => {
    it("accepts the supported subset and annotations", () => {
      expect(
        schemaProblems({
          type: "object",
          title: "t",
          description: "d",
          required: ["a"],
          properties: { a: { type: "array", items: { enum: ["x"] }, minItems: 0, maxItems: 3 } },
        }),
      ).toEqual([]);
    });

    it.each([
      ["$ref", { $ref: "#/x" }],
      ["anyOf", { anyOf: [{ type: "string" }] }],
      ["pattern", { type: "string", pattern: "a" }],
      ["additionalProperties", { type: "object", additionalProperties: false }],
      ["nested unsupported", { type: "object", properties: { a: { format: "email" } } }],
    ])("rejects unsupported keyword: %s", (_n, schema) => {
      expect(schemaProblems(schema).join(" ")).toContain("unsupported keyword");
    });

    it("rejects malformed keyword values", () => {
      expect(schemaProblems({ type: "date" }).length).toBeGreaterThan(0);
      expect(schemaProblems({ required: "a" }).length).toBeGreaterThan(0);
      expect(schemaProblems({ minItems: -1 }).length).toBeGreaterThan(0);
      expect(schemaProblems({ minimum: "1" }).length).toBeGreaterThan(0);
      expect(schemaProblems({ enum: [] }).length).toBeGreaterThan(0);
      expect(schemaProblems({ properties: { a: "x" } }).length).toBeGreaterThan(0);
      expect(schemaProblems("string").length).toBeGreaterThan(0);
      expect(schemaProblems([]).length).toBeGreaterThan(0);
    });

    it("rejects an oversize schema", () => {
      const big = { enum: ["x".repeat(MAX_OUTPUT_SCHEMA_BYTES)] };
      expect(schemaProblems(big).join(" ")).toContain("larger than");
    });

    it("rejects a schema nested too deep", () => {
      let s: JsonObject = { type: "string" };
      for (let i = 0; i < MAX_OUTPUT_SCHEMA_DEPTH; i++) s = { type: "array", items: s };
      expect(schemaProblems(s).join(" ")).toContain("deeper than");
      let okSchema: JsonObject = { type: "string" };
      for (let i = 0; i < MAX_OUTPUT_SCHEMA_DEPTH - 1; i++)
        okSchema = { type: "array", items: okSchema };
      expect(schemaProblems(okSchema)).toEqual([]);
    });
  });
});
