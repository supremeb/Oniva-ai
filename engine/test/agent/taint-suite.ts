/**
 * Taint tracking (U4, R7, KTD7), run against every storage backend.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import type { HostPolicy, ToolAuthorizationRequest } from "../../src/agent/policy.js";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { markTainted, TAINT_SOURCE_CAP, type TaintState, taintDoc } from "../../src/agent/taint.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { defineTool, INTERNAL_NATIVE_TOOLS } from "../../src/tools/registry.js";
import { type AgentBackend, type AgentWorker, allowAll, untilAborted, waitFor } from "./support.js";

const FIXTURE = JSON.parse(
  readFileSync(new URL("../support/internal-native-tools.json", import.meta.url), "utf8"),
) as string[];

const AGENT = { systemPrompt: "s", tools: ["local", "web_read", "safe_gateway", "update_todo"] };

interface StaticTool {
  source?: "gateway" | "native";
  internal?: boolean;
  untrustedOutput?: boolean;
  replay?: "safe" | "unsafe";
  execute?: () => Promise<{ content: string }> | { content: string };
}

const tool = (name: string, extra: StaticTool = {}) =>
  defineTool({
    name,
    description: name,
    parameters: z.object({}),
    replay: extra.replay ?? "safe",
    ...(extra.source === undefined ? {} : { source: extra.source }),
    ...(extra.internal === undefined ? {} : { internal: extra.internal }),
    ...(extra.untrustedOutput === undefined ? {} : { untrustedOutput: extra.untrustedOutput }),
    execute: extra.execute ?? (() => ({ content: `${name} out` })),
  });

/** A policy that records every authorize request and allows the call. */
function recording(): { policy: HostPolicy; seen: ToolAuthorizationRequest[] } {
  const seen: ToolAuthorizationRequest[] = [];
  return {
    seen,
    policy: {
      resolveModelRequest: allowAll.resolveModelRequest,
      authorizeTool: (req) => {
        seen.push(req);
        return { decision: "allow" };
      },
    },
  };
}

const taintOf = (w: AgentWorker, id: string): Promise<TaintState> =>
  w.harness.session.document(taintDoc, id);

export function defineTaintSuite(backend: AgentBackend): void {
  describe(`taint (${backend.name})`, () => {
    it("INTERNAL_NATIVE_TOOLS matches the shared fixture", () => {
      expect([...INTERNAL_NATIVE_TOOLS]).toEqual(FIXTURE);
      expect([...INTERNAL_NATIVE_TOOLS]).toEqual([...INTERNAL_NATIVE_TOOLS].sort());
    });

    it("a gateway tool taints only when the manifest marks it untrusted", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("safe_gateway", {}, { id: "c1" }),
        fakeToolCall("web_read", {}, { id: "c2" }),
        fakeText("done"),
      );
      const { policy } = recording();
      const w = await fx.open({
        policy,
        tools: [
          tool("safe_gateway", { source: "gateway", untrustedOutput: false }),
          tool("web_read", { source: "gateway", untrustedOutput: true }),
        ],
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "read" })).wait();

      const taint = await taintOf(w, conv.id);
      expect(taint.tainted).toBe(true);
      expect(taint.total).toBe(1);
      expect(taint.sources).toEqual([
        { kind: "tool_result", name: "web_read", ref: "c2", at: expect.any(Number) },
      ]);
      await w.crash();
    });

    it("a native tool taints unless it is internal or allowlisted", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(
        fakeToolCall("local", {}, { id: "c1" }),
        fakeToolCall("update_todo", {}, { id: "c2" }),
        fakeToolCall("flagged", {}, { id: "c3" }),
        fakeText("done"),
      );
      const w = await fx.open({
        policy: allowAll,
        tools: [tool("local"), tool("update_todo"), tool("flagged", { internal: true })],
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["local", "update_todo", "flagged"] },
      });
      await (await conv.submit({ text: "go" })).wait();

      const taint = await taintOf(w, conv.id);
      expect(taint.tainted).toBe(true);
      expect(taint.total).toBe(1);
      expect(taint.sources.map((s) => s.name)).toEqual(["local"]);
      await w.crash();
    });

    it("an untrusted submission taints with kind input before the model request", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("local", {}, { id: "c1" }), fakeText("done"));
      const { policy, seen } = recording();
      const w = await fx.open({ policy, tools: [tool("local")] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["local"] },
      });
      const sub = await conv.submit({ text: "see the picture", untrusted: true });
      await sub.wait();

      const taint = await taintOf(w, conv.id);
      expect(taint.tainted).toBe(true);
      expect(taint.sources).toEqual([
        { kind: "input", name: "input", ref: sub.id, at: expect.any(Number) },
        { kind: "tool_result", name: "local", ref: "c1", at: expect.any(Number) },
      ]);
      // The model's first tool call was authorized after the taint was recorded.
      const authorize = seen.find((r) => r.toolName === "local");
      expect(authorize?.taint.tainted).toBe(true);
      await w.crash();
    });

    it("a write submission never taints", async () => {
      const fx = await backend.fixture();
      const w = await fx.open({ policy: allowAll, tools: [] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: [] },
      });
      await conv.submit({ text: "just a note", type: "write", untrusted: true });
      expect((await taintOf(w, conv.id)).tainted).toBe(false);
      await w.crash();
    });

    it("the source list caps at TAINT_SOURCE_CAP while total counts every event", async () => {
      const fx = await backend.fixture();
      const w = await fx.open({ policy: allowAll, tools: [] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: [] },
      });
      for (let i = 0; i < TAINT_SOURCE_CAP + 5; i++) {
        await w.harness.runtime.commit((tx) =>
          markTainted(tx, conv.id, { kind: "input", name: "input", ref: `s${i}` }),
        );
      }
      const taint = await taintOf(w, conv.id);
      expect(taint.total).toBe(TAINT_SOURCE_CAP + 5);
      expect(taint.sources).toHaveLength(TAINT_SOURCE_CAP);
      expect(taint.sources[0]?.ref).toBe("s0");
      await w.crash();
    });

    it("an authorized call carries the tool task id, its source and the taint state", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("local", {}, { id: "c1" }), fakeText("done"));
      const { policy, seen } = recording();
      const w = await fx.open({ policy, tools: [tool("local")] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["local"] },
      });
      await (await conv.submit({ text: "go" })).wait();

      const task = w.harness.runtime
        .tasks()
        .find(
          (t) =>
            t.kind === "agent.tool" &&
            (t.input as { call: { name: string } }).call.name === "local",
        );
      const authorize = seen.find((r) => r.toolName === "local");
      expect(authorize).toMatchObject({
        toolTaskId: task?.id,
        toolSource: "native",
        taint: { tainted: false, total: 0, sources: [] },
      });
      await w.crash();
    });

    it("taint is durable: it survives a reopen with the tool result", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeToolCall("web_read", {}, { id: "c1" }), fakeText("done"));
      const options = () => ({
        policy: allowAll,
        tools: [tool("web_read", { source: "gateway", untrustedOutput: true })],
      });
      const w1 = await fx.open(options());
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "read" })).wait();
      expect((await taintOf(w1, conv.id)).total).toBe(1);
      await w1.crash();

      const w2 = await fx.open(options());
      const taint = await taintOf(w2, conv.id);
      expect(taint.tainted).toBe(true);
      expect(taint.sources[0]).toMatchObject({ kind: "tool_result", name: "web_read", ref: "c1" });
      const result = (await w2.harness.conversation(conv.id).entries()).find(
        (e) => e.kind === "tool_result",
      );
      expect(result?.payload).toMatchObject({ toolCallId: "c1", status: "ok" });
      await w2.crash();
    });

    it("recovery taints for an interrupted unsafe call", async () => {
      const fx = await backend.fixture();
      let entered = false;
      const block = tool("block", {
        execute: async () => {
          entered = true;
          return { content: "never" };
        },
      });
      const unsafe = defineTool({
        name: "block",
        description: "block",
        parameters: z.object({}),
        execute: async (_a, api) => {
          entered = true;
          return untilAborted(api.signal);
        },
      });
      const options = () => ({ policy: allowAll, tools: [unsafe] });
      fx.provider.enqueue(fakeToolCall("block", {}, { id: "c1" }));
      const w1 = await fx.open(options());
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["block"] },
      });
      const sub = await conv.submit({ text: "block" });
      await waitFor(() => entered);
      await w1.crash();
      void block;

      fx.provider.enqueue(fakeText("after"));
      const w2 = await fx.open(options());
      expect((await w2.harness.submission(sub.id).wait()).status).toBe("done");
      const taint = await taintOf(w2, conv.id);
      expect(taint.tainted).toBe(true);
      expect(taint.sources.some((s) => s.name === "block" && s.ref === "c1")).toBe(true);
      const result = (await w2.harness.conversation(conv.id).entries()).find(
        (e) => e.kind === "tool_result",
      );
      expect(result?.payload).toMatchObject({ toolCallId: "c1", status: "interrupted" });
      await w2.crash();
    });

    it("a subagent child's untrusted read taints the root for the parent's next call", async () => {
      const fx = await backend.fixture();
      const research = createSubagentTool({ name: "research", tools: ["scrape"] });
      fx.provider.enqueue(
        fakeToolCall("research", { prompt: "look" }, { id: "r1" }),
        fakeToolCall("scrape", {}, { id: "s1" }),
        fakeText("scraped"),
        fakeToolCall("deploy", {}, { id: "d1" }),
        fakeText("done"),
      );
      const { policy, seen } = recording();
      const w = await fx.open({
        policy,
        tools: [
          research,
          tool("scrape"),
          tool("deploy", { source: "gateway", untrustedOutput: false }),
        ],
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["research", "deploy"] },
      });
      await (await conv.submit({ text: "investigate" })).wait();

      const scrape = seen.find((r) => r.toolName === "scrape");
      const deploy = seen.find((r) => r.toolName === "deploy");
      const researchCall = seen.find((r) => r.toolName === "research");
      expect(scrape?.taint.tainted).toBe(false);
      expect(researchCall?.taint.tainted).toBe(false);
      expect(deploy?.taint.tainted).toBe(true);
      expect(deploy?.conversation.id).toBe(conv.id);

      const taint = await taintOf(w, conv.id);
      expect(taint.sources.map((s) => s.name)).toEqual(["scrape"]);
      await w.crash();
    });
  });
}
