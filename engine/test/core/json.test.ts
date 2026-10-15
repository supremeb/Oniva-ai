import { describe, expect, it } from "vitest";
import { InvalidJson } from "../../src/core/errors.js";
import { assertJson, isJson, jsonByteLength, parseJson } from "../../src/core/json.js";

function sparse(): unknown[] {
  const arr: unknown[] = [1];
  arr[2] = 3;
  return arr;
}

describe("strict json", () => {
  it("accepts nested plain data", () => {
    const shared = { k: 1 };
    const value = {
      a: [1, "two", true, null, { b: [{ c: -0.5 }] }],
      d: {},
      e: [],
      s1: shared,
      s2: shared,
      nullProto: Object.assign(Object.create(null), { x: 1 }),
    };
    expect(() => assertJson(value)).not.toThrow();
    expect(isJson(value)).toBe(true);
  });

  it.each([
    ["undefined at top", undefined, "$"],
    ["undefined property", { a: { b: undefined } }, "$.a.b"],
    ["undefined in array", [1, undefined], "$[1]"],
    ["NaN", { n: Number.NaN }, "$.n"],
    ["Infinity", [Number.POSITIVE_INFINITY], "$[0]"],
    ["function", { f: () => 1 }, "$.f"],
    ["symbol", { s: Symbol("x") }, "$.s"],
    ["bigint", { b: 1n }, "$.b"],
    ["Date", { d: new Date(0) }, "$.d"],
    ["Map", new Map(), "$"],
    ["class instance", { c: new (class Foo {})() }, "$.c"],
    ["sparse array", sparse(), "$[1]"],
  ])("rejects %s", (_label, value, path) => {
    expect(isJson(value)).toBe(false);
    try {
      assertJson(value);
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(InvalidJson);
      expect((err as InvalidJson).path).toBe(path);
    }
  });

  it("rejects cycles", () => {
    const a: Record<string, unknown> = { x: 1 };
    a.self = a;
    expect(() => assertJson(a)).toThrow(InvalidJson);
    const arr: unknown[] = [];
    arr.push([arr]);
    expect(isJson(arr)).toBe(false);
  });

  it("parses strictly and measures UTF-8 size", () => {
    expect(parseJson('{"a":[1,2]}')).toEqual({ a: [1, 2] });
    expect(() => parseJson("{bad")).toThrow(InvalidJson);
    expect(jsonByteLength({ s: "é" })).toBe(Buffer.byteLength('{"s":"é"}'));
  });
});
