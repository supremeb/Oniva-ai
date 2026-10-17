import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { JsonValue } from "../../src/core/json.js";
import { argsHash, canonicalJson } from "../../src/worker/args-hash.js";

interface Vector {
  name: string;
  value: JsonValue;
  canonical: string;
  hash: string;
}

const vectors = (
  JSON.parse(
    readFileSync(new URL("../support/args-hash-vectors.json", import.meta.url), "utf8"),
  ) as {
    cases: Vector[];
  }
).cases;

describe("args hash", () => {
  it("has shared vectors", () => {
    expect(vectors.length).toBeGreaterThan(5);
  });

  for (const v of vectors) {
    it(`matches the shared vector: ${v.name}`, () => {
      expect(canonicalJson(v.value)).toBe(v.canonical);
      expect(argsHash(v.value)).toBe(v.hash);
    });
  }

  it("does not depend on key insertion order", () => {
    expect(argsHash({ a: 1, b: { c: 2, d: 3 } })).toBe(argsHash({ b: { d: 3, c: 2 }, a: 1 }));
  });

  it("changes when any argument changes", () => {
    expect(argsHash({ code: "print(1)" })).not.toBe(argsHash({ code: "print(2)" }));
  });
});
