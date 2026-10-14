/**
 * Helpers for watch and server tests: a tool that blocks on gates and writes
 * partial output, and a frame-applying client view.
 */
import { z } from "zod";
import type { JsonValue } from "../../src/core/json.js";
import { defineTool } from "../../src/tools/registry.js";
import { applyJsonPatch } from "../../src/watch/patch.js";
import type { WatchFrame } from "../../src/watch/stream.js";

export interface Gate {
  promise: Promise<void>;
  open(): void;
}

export function gate(): Gate {
  let open!: () => void;
  const promise = new Promise<void>((r) => {
    open = r;
  });
  return { promise, open };
}

/** `work` (abortable) writes "a", waits for gate 1, writes "b", waits for gate 2, then returns. */
export function gatedTool(gates: [Gate, Gate]) {
  return defineTool({
    name: "work",
    description: "Does slow work",
    parameters: z.object({}),
    execute: async (_args, api) => {
      const aborted = new Promise<never>((_, reject) => {
        if (api.signal.aborted) reject(api.signal.reason);
        api.signal.addEventListener("abort", () => reject(api.signal.reason), { once: true });
      });
      aborted.catch(() => {});
      api.output("a");
      await Promise.race([gates[0].promise, aborted]);
      api.output("b");
      await Promise.race([gates[1].promise, aborted]);
      return { content: "worked" };
    },
  });
}

/** Applies frames the way a client would. */
export class ClientView {
  cursor = -1;
  view: JsonValue | null = null;
  readonly frames: WatchFrame[] = [];

  apply(frame: WatchFrame): void {
    this.frames.push(frame);
    if ("snapshot" in frame) {
      this.view = structuredClone(frame.snapshot) as unknown as JsonValue;
    } else {
      if (this.view === null) throw new Error("patch before snapshot");
      if (frame.cursor <= this.cursor) throw new Error("cursor went backwards");
      this.view = applyJsonPatch(this.view, frame.patch);
    }
    this.cursor = frame.cursor;
  }
}
