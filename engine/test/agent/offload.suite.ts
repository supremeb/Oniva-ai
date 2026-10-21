/**
 * Tier 0 offload at write time (U12), run against every storage backend: a
 * tool result over `offloadTokens` is stored out of context through the
 * host's `OffloadStore`, and the entry keeps a short preview and a pointer.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import type { AgentErrorInfo } from "../../src/agent/hooks.js";
import type { OffloadPutRequest, OffloadStore } from "../../src/agent/offload.js";
import { taintDoc } from "../../src/agent/taint.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, untilAborted, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const payload = (e: EntryRecord): Any => e.payload as Any;
const results = (entries: EntryRecord[]) =>
  entries.filter((e) => e.kind === "tool_result").map(payload);

/** 600 lines, about 5000 characters: ~1250 estimated tokens. */
const BIG = Array.from({ length: 600 }, (_, i) => `line ${i}`).join("\n");

/** An in-memory store keyed like the real one: one object per `runId:toolCallId`. */
export class MemoryOffloadStore implements OffloadStore {
  readonly objects = new Map<string, { path: string; text: string }>();
  readonly calls: OffloadPutRequest[] = [];
  failWith: Error | null = null;
  /** Runs after the object is stored and before `put` answers. */
  afterStore: ((req: OffloadPutRequest) => Promise<void>) | null = null;

  async put(req: OffloadPutRequest) {
    this.calls.push(req);
    if (this.failWith) throw this.failWith;
    const key = `${req.runId}:${req.toolCallId}`;
    let obj = this.objects.get(key);
    if (!obj) {
      obj = { path: `tool_results/${req.toolCallId}.txt`, text: req.text };
      this.objects.set(key, obj);
    }
    if (this.afterStore) await this.afterStore(req);
    return { path: obj.path, bytes: Buffer.byteLength(obj.text, "utf8") };
  }
}

function bigTool(
  extra: {
    name?: string;
    content?: Any;
    details?: Any;
    untrustedOutput?: boolean;
    executed?: number[];
  } = {},
) {
  return defineTool({
    name: extra.name ?? "big",
    description: "Returns a lot of text",
    parameters: z.object({}),
    replay: "safe",
    ...(extra.untrustedOutput === undefined ? {} : { untrustedOutput: extra.untrustedOutput }),
    execute: async () => {
      extra.executed?.push(1);
      return {
        content: extra.content ?? BIG,
        ...(extra.details === undefined ? {} : { details: extra.details }),
      };
    },
  });
}

const agent = (compaction?: Record<string, unknown>, tools = ["big"]) => ({
  systemPrompt: "s",
  tools,
  ...(compaction ? { compaction } : {}),
});

const conversationOf = (compaction?: Record<string, unknown>, tools?: string[]) => ({
  tenantId: "t1",
  projectId: "p1",
  agent: agent(compaction, tools),
});

export function defineOffloadSuite(backend: AgentBackend): void {
  describe(`tier 0 offload (${backend.name})`, () => {
    it("leaves a result below the threshold unchanged and never calls the store", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("big", {}, { id: "c1" }), fakeText("done"));
      const store = new MemoryOffloadStore();
      const w = await fx.open({
        tools: [bigTool({ content: "small output" })],
        offload: store,
      });
      const conv = await w.harness.createConversation(conversationOf({ offloadTokens: 100 }));
      await (await conv.submit({ text: "go" })).wait();
      const [r] = results(await conv.entries());
      expect(r.content).toBe("small output");
      expect(r.details).toBeNull();
      expect(store.calls).toHaveLength(0);
      await w.crash();
    });

    it("stores a result above the threshold and keeps a preview and a pointer", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("big", {}, { id: "c1" }), fakeText("done"));
      const store = new MemoryOffloadStore();
      const w = await fx.open({ tools: [bigTool({ details: { rows: 3 } })], offload: store });
      const conv = await w.harness.createConversation(conversationOf({ offloadTokens: 100 }));
      const settled = await (await conv.submit({ text: "go" })).wait();
      expect(settled.status).toBe("done");

      expect(store.calls).toHaveLength(1);
      const call = store.calls[0] as OffloadPutRequest;
      expect(call.toolCallId).toBe("c1");
      expect(call.toolName).toBe("big");
      expect(call.conversation.id).toBe(conv.id);
      expect(call.conversation.tenantId).toBe("t1");
      expect(call.text).toBe(BIG);

      const [r] = results(await conv.entries());
      expect(r.status).toBe("ok");
      expect(r.content).toContain("line 0\nline 1\nline 2");
      expect(r.content).not.toContain("line 599");
      expect(r.content).toContain("saved to tool_results/c1.txt");
      expect(r.content).toContain("read_file with offset and limit");
      expect(r.content).toMatch(/full output \(~\d+ tokens\)/);
      expect(r.content.length).toBeLessThan(2_600);
      expect(r.details).toEqual({
        rows: 3,
        offloaded: { path: "tool_results/c1.txt", bytes: Buffer.byteLength(BIG) },
      });

      // The next model request sees the preview, not the full text.
      const sent = JSON.stringify(fx.provider.requests.at(-1)?.messages);
      expect(sent).toContain("tool_results/c1.txt");
      expect(sent).not.toContain("line 599");
      await w.crash();
    });

    it("the preview is at most 40 lines and about 2000 characters", async () => {
      const fx = await backend.fixture();
      const wide = Array.from({ length: 100 }, (_, i) => `${i}:${"x".repeat(100)}`).join("\n");
      fx.provider.enqueue(fakeToolCall("big", {}, { id: "c1" }), fakeText("done"));
      const store = new MemoryOffloadStore();
      const w = await fx.open({ tools: [bigTool({ content: wide })], offload: store });
      const conv = await w.harness.createConversation(conversationOf({ offloadTokens: 100 }));
      await (await conv.submit({ text: "go" })).wait();
      const [r] = results(await conv.entries());
      const preview: string = r.content.split("\n\n[full output")[0];
      expect(preview.length).toBeLessThanOrEqual(2_000);
      expect(preview.split("\n").length).toBeLessThanOrEqual(40);
      expect(preview.startsWith("0:xxx")).toBe(true);
      await w.crash();
    });

    it("keeps the original result, succeeds and reports a warning when the store fails", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("big", {}, { id: "c1" }), fakeText("done"));
      const store = new MemoryOffloadStore();
      store.failWith = new Error("store is down");
      const reported: Array<{ err: unknown; info: AgentErrorInfo }> = [];
      const w = await fx.open({
        tools: [bigTool()],
        offload: store,
        onError: (err, info) => reported.push({ err, info }),
      });
      const conv = await w.harness.createConversation(conversationOf({ offloadTokens: 100 }));
      const settled = await (await conv.submit({ text: "go" })).wait();
      expect(settled.status).toBe("done");
      const [r] = results(await conv.entries());
      expect(r.status).toBe("ok");
      expect(r.content).toBe(BIG);
      expect(r.details).toBeNull();
      const warning = reported.find((x) => x.info.during === "offload");
      expect(warning).toBeDefined();
      expect(String(warning?.err)).toContain("store is down");
      expect(warning?.info.conversationId).toBe(conv.id);
      await w.crash();
    });

    it("a rerun after a crash between put and commit leaves one object and one tool_result", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("big", {}, { id: "c1" }));
      const store = new MemoryOffloadStore();
      const executed: number[] = [];
      const w1 = await fx.open({ tools: [bigTool({ executed })], offload: store });
      const conv1 = await w1.harness.createConversation(conversationOf({ offloadTokens: 100 }));
      // The first process dies right after the object is stored.
      store.afterStore = async (req) => {
        store.afterStore = null;
        void w1.crash();
        await untilAborted(req.signal as AbortSignal);
      };
      const sub = await conv1.submit({ text: "go" });
      await waitFor(() => store.objects.size === 1);
      await w1.crash();

      fx.provider.enqueue(fakeText("done"));
      const w2 = await fx.open({ tools: [bigTool({ executed })], offload: store });
      const settled = await w2.harness.submission(sub.id).wait();
      expect(settled.status).toBe("done");
      expect(store.objects.size).toBe(1);
      expect(store.calls.length).toBeGreaterThanOrEqual(2);
      const rs = results(await w2.harness.conversation(conv1.id).entries());
      expect(rs).toHaveLength(1);
      expect(rs[0].details.offloaded.path).toBe("tool_results/c1.txt");
      await w2.crash();
    });

    it("excludeTools bypasses the offload", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("keep", {}, { id: "c1" }),
        fakeToolCall("big", {}, { id: "c2" }),
        fakeText("done"),
      );
      const store = new MemoryOffloadStore();
      const w = await fx.open({
        tools: [bigTool({ name: "keep" }), bigTool()],
        offload: store,
      });
      const conv = await w.harness.createConversation(
        conversationOf({ offloadTokens: 100, excludeTools: ["keep"] }, ["keep", "big"]),
      );
      await (await conv.submit({ text: "go" })).wait();
      const rs = results(await conv.entries());
      expect(rs.find((r) => r.toolName === "keep").content).toBe(BIG);
      expect(rs.find((r) => r.toolName === "big").details.offloaded.path).toBe(
        "tool_results/c2.txt",
      );
      expect(store.calls.map((c) => c.toolCallId)).toEqual(["c2"]);
      await w.crash();
    });

    it("flattens content parts the way the result cap does", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("big", {}, { id: "c1" }), fakeText("done"));
      const store = new MemoryOffloadStore();
      const parts = [
        { type: "text", text: BIG },
        { type: "text", text: "tail part" },
      ];
      const w = await fx.open({ tools: [bigTool({ content: parts })], offload: store });
      const conv = await w.harness.createConversation(conversationOf({ offloadTokens: 100 }));
      await (await conv.submit({ text: "go" })).wait();
      expect(store.calls[0]?.text).toBe(`${BIG}\ntail part`);
      const [r] = results(await conv.entries());
      expect(typeof r.content).toBe("string");
      expect(r.details.offloaded.path).toBe("tool_results/c1.txt");
      await w.crash();
    });

    it("does nothing with offloadTokens 0 (the default), or without a store", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("big", {}, { id: "c1" }),
        fakeText("one"),
        fakeToolCall("big", {}, { id: "c2" }),
        fakeText("two"),
      );
      const store = new MemoryOffloadStore();
      const w = await fx.open({ tools: [bigTool()], offload: store });
      const off = await w.harness.createConversation(conversationOf());
      await (await off.submit({ text: "go" })).wait();
      expect(store.calls).toHaveLength(0);
      expect(results(await off.entries())[0].content).toBe(BIG);
      await w.crash();

      const w2 = await fx.open({ tools: [bigTool()] });
      const noStore = await w2.harness.createConversation(conversationOf({ offloadTokens: 100 }));
      await (await noStore.submit({ text: "go" })).wait();
      expect(results(await noStore.entries())[0].content).toBe(BIG);
      await w2.crash();
    });

    it("harness-level compaction settings enable it and a conversation can switch it off", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("big", {}, { id: "c1" }),
        fakeText("one"),
        fakeToolCall("big", {}, { id: "c2" }),
        fakeText("two"),
      );
      const store = new MemoryOffloadStore();
      const w = await fx.open({
        tools: [bigTool()],
        offload: store,
        compaction: { offloadTokens: 100 },
      });
      const on = await w.harness.createConversation(conversationOf());
      await (await on.submit({ text: "go" })).wait();
      expect(store.calls.map((c) => c.toolCallId)).toEqual(["c1"]);
      const off = await w.harness.createConversation(conversationOf({ offloadTokens: 0 }));
      await (await off.submit({ text: "go" })).wait();
      expect(store.calls).toHaveLength(1);
      expect(results(await off.entries())[0].content).toBe(BIG);
      await w.crash();
    });

    it("an untrusted result still taints the conversation", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("big", {}, { id: "c1" }), fakeText("done"));
      const store = new MemoryOffloadStore();
      const w = await fx.open({ tools: [bigTool({ untrustedOutput: true })], offload: store });
      const conv = await w.harness.createConversation(conversationOf({ offloadTokens: 100 }));
      await (await conv.submit({ text: "go" })).wait();
      expect(results(await conv.entries())[0].details.offloaded).toBeDefined();
      const taint = await w.harness.session.document(taintDoc, conv.id);
      expect(taint.tainted).toBe(true);
      await w.crash();
    });
  });
}
