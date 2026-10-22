import { describe, expect, it } from "vitest";
import {
  buildOffloadPreview,
  OFFLOAD_PREVIEW_CHARS,
  OFFLOAD_PREVIEW_LINES,
  offloadPointer,
  resultText,
} from "../../src/agent/offload.js";

describe("offload preview", () => {
  it("keeps the first 40 lines of a short-line text", () => {
    const text = Array.from({ length: 100 }, (_, i) => `l${i}`).join("\n");
    const p = buildOffloadPreview(text);
    expect(p.split("\n")).toHaveLength(OFFLOAD_PREVIEW_LINES);
    expect(p.startsWith("l0\nl1")).toBe(true);
  });
  it("bounds the preview by characters, cutting a long line", () => {
    const p = buildOffloadPreview("y".repeat(10_000));
    expect(p).toHaveLength(OFFLOAD_PREVIEW_CHARS);
  });
  it("returns a short text whole", () => {
    expect(buildOffloadPreview("a\nb")).toBe("a\nb");
  });
  it("names the path and the estimated tokens in the pointer", () => {
    expect(offloadPointer("tool_results/x.txt", 1_234)).toBe(
      "[full output (~1234 tokens) saved to tool_results/x.txt; use read_file with offset and limit to read more]",
    );
  });
  it("flattens parts with newlines", () => {
    expect(
      resultText({
        content: [
          { type: "text", text: "a" },
          { type: "text", text: "b" },
        ],
      }),
    ).toBe("a\nb");
    expect(resultText({ content: "s" })).toBe("s");
  });
});
