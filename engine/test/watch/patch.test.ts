import { describe, expect, it } from "vitest";
import type { JsonValue } from "../../src/core/json.js";
import {
  applyJsonPatch,
  diffJson,
  escapePointerToken,
  InvalidPatch,
} from "../../src/watch/patch.js";

function roundTrip(before: JsonValue, after: JsonValue): void {
  const ops = diffJson(before, after);
  const frozen = structuredClone(before);
  expect(applyJsonPatch(before, ops)).toEqual(after);
  // The input is never mutated.
  expect(before).toEqual(frozen);
}

describe("JSON Patch", () => {
  it("produces no operations for equal values", () => {
    expect(diffJson({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toEqual([]);
  });

  it("adds, removes and replaces object members", () => {
    const ops = diffJson({ a: 1, b: 2, c: { d: 1 } }, { a: 1, c: { d: 2 }, e: null });
    expect(ops).toEqual(
      expect.arrayContaining([
        { op: "remove", path: "/b" },
        { op: "replace", path: "/c/d", value: 2 },
        { op: "add", path: "/e", value: null },
      ]),
    );
    expect(ops).toHaveLength(3);
  });

  it("appends to and truncates arrays by index", () => {
    expect(diffJson([1, 2], [1, 2, 3, 4])).toEqual([
      { op: "add", path: "/2", value: 3 },
      { op: "add", path: "/3", value: 4 },
    ]);
    expect(diffJson([1, 2, 3, 4], [1, 2])).toEqual([
      { op: "remove", path: "/3" },
      { op: "remove", path: "/2" },
    ]);
  });

  it("replaces values of a different type, including the root", () => {
    expect(diffJson({ a: [1] }, { a: { 0: 1 } })).toEqual([
      { op: "replace", path: "/a", value: { 0: 1 } },
    ]);
    expect(diffJson([1], "x")).toEqual([{ op: "replace", path: "", value: "x" }]);
  });

  it("escapes pointer tokens", () => {
    expect(escapePointerToken("a/b~c")).toBe("a~1b~0c");
    const ops = diffJson({}, { "a/b~c": 1 });
    expect(ops).toEqual([{ op: "add", path: "/a~1b~0c", value: 1 }]);
    expect(applyJsonPatch({}, ops)).toEqual({ "a/b~c": 1 });
  });

  it("round-trips assorted documents", () => {
    roundTrip(
      { items: [{ id: 1, s: "a" }] },
      {
        items: [
          { id: 1, s: "ab" },
          { id: 2, s: "" },
        ],
      },
    );
    roundTrip({ x: { y: [1, [2, 3]] } }, { x: { y: [[2], 1] }, z: true });
    roundTrip({ list: [1, 2, 3] }, { list: [] });
    roundTrip(null, { a: 1 });
    roundTrip({ a: "" }, {});
  });

  it("supports '-' appends and rejects bad paths", () => {
    expect(applyJsonPatch({ a: [1] }, [{ op: "add", path: "/a/-", value: 2 }])).toEqual({
      a: [1, 2],
    });
    expect(() => applyJsonPatch({}, [{ op: "remove", path: "/missing" }])).toThrow(InvalidPatch);
    expect(() => applyJsonPatch({ a: [] }, [{ op: "add", path: "/a/5", value: 1 }])).toThrow(
      InvalidPatch,
    );
    expect(() => applyJsonPatch({}, [{ op: "replace", path: "nope", value: 1 }])).toThrow(
      InvalidPatch,
    );
  });
});
