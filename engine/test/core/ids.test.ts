import { describe, expect, it } from "vitest";
import { createIdGenerator, ID_PREFIXES, idKindOf, isId, newId } from "../../src/core/ids.js";

const UUID_V7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("ids", () => {
  it("mints kind-prefixed UUIDv7 strings", () => {
    for (const [kind, prefix] of Object.entries(ID_PREFIXES)) {
      const id = newId(kind as keyof typeof ID_PREFIXES);
      expect(id.startsWith(prefix)).toBe(true);
      expect(id.slice(prefix.length)).toMatch(UUID_V7);
    }
    expect(ID_PREFIXES).toMatchObject({
      conversation: "conv_",
      entry: "ent_",
      task: "task_",
      submission: "subm_",
      approval: "appr_",
    });
  });

  it("keeps creation order for ids minted in the same millisecond", () => {
    const gen = createIdGenerator({ now: () => 1_700_000_000_000 });
    const ids = Array.from({ length: 5000 }, () => gen.next("task"));
    expect([...ids].sort()).toEqual(ids);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("stays ordered when the wall clock steps backwards", () => {
    let t = 1_700_000_000_500;
    const gen = createIdGenerator({ now: () => t });
    const a = gen.next("entry");
    t -= 400;
    const b = gen.next("entry");
    expect(b > a).toBe(true);
  });

  it("orders by timestamp across milliseconds", () => {
    let t = 1_700_000_000_000;
    const gen = createIdGenerator({ now: () => t });
    const a = gen.next("conversation");
    t += 1;
    const b = gen.next("conversation");
    expect(b > a).toBe(true);
    expect(gen.timestampOf(a)).toBe(1_700_000_000_000);
    expect(gen.timestampOf(b)).toBe(1_700_000_000_001);
  });

  it("recognises ids by kind", () => {
    const id = newId("approval");
    expect(isId("approval", id)).toBe(true);
    expect(isId("task", id)).toBe(false);
    expect(idKindOf(id)).toBe("approval");
    expect(idKindOf("nope_123")).toBeUndefined();
    expect(isId("approval", "appr_not-a-uuid")).toBe(false);
  });
});
