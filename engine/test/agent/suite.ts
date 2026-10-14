/**
 * The agent loop suite (U7), run against every storage backend.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import type { AgentHooks } from "../../src/agent/hooks.js";
import type { HostPolicy } from "../../src/agent/policy.js";
import { toolOutputDoc } from "../../src/agent/progress.js";
import { usageDoc } from "../../src/agent/usage.js";
import type { JsonValue } from "../../src/core/json.js";
import { fakeError, fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { ModelRequest } from "../../src/models/types.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, allowAll, untilAborted, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to entry payloads
type Any = any;
const payload = (e: EntryRecord): Any => e.payload as Any;
const kinds = (entries: EntryRecord[]) => entries.map((e) => e.kind);
const results = (entries: EntryRecord[]) =>
  entries.filter((e) => e.kind === "tool_result").map(payload);

function readTool(log: string[] = []) {
  return defineTool({
    name: "read",
    description: "Read a file",
    parameters: z.object({ path: z.string() }),
    replay: "safe",
    execute: async (args) => {
      log.push(`read:${args.path}`);
      return { content: `contents of ${args.path}` };
    },
  });
}

function twoCalls(a: JsonValue, b: JsonValue) {
  return {
    steps: [
      { type: "tool_call" as const, id: "c1", name: "read", arguments: a as Any },
      { type: "tool_call" as const, id: "c2", name: "read", arguments: b as Any },
    ],
  };
}

const AGENT = { systemPrompt: "You are helpful.", tools: ["read"] };

export function defineAgentSuite(backend: AgentBackend): void {
  describe(`agent loop (${backend.name})`, () => {
    it("answers one input with two parallel tool calls and a final answer", async () => {
      const fx = await backend.fixture();
      const seenPolicy: Any[] = [];
      const policy: HostPolicy = {
        resolveModelRequest: (req) => {
          seenPolicy.push({ tenant: req.conversation.tenantId, round: req.round });
          return allowAll.resolveModelRequest(req);
        },
        authorizeTool: allowAll.authorizeTool,
      };
      const log: string[] = [];
      fx.provider.enqueue(twoCalls({ path: "a" }, { path: "b" }), fakeText("Both read."));
      const w = await fx.open({ tools: [readTool(log)], policy });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "read a and b" });
      const settled = await sub.wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).text).toBe("Both read.");

      const entries = await conv.entries();
      // The system entry is appended by the first request that uses it.
      expect(kinds(entries)).toEqual([
        "user",
        "system",
        "assistant",
        "tool_result",
        "tool_result",
        "assistant",
      ]);
      expect(
        results(entries)
          .map((r) => [r.toolCallId, r.status, r.content])
          .sort(),
      ).toEqual([
        ["c1", "ok", "contents of a"],
        ["c2", "ok", "contents of b"],
      ]);
      expect(log.sort()).toEqual(["read:a", "read:b"]);

      // The second request saw both results after the assistant's calls.
      const second = fx.provider.requests[1] as ModelRequest;
      expect(second.messages.map((m) => m.role)).toEqual([
        "system",
        "user",
        "assistant",
        "tool_result",
        "tool_result",
      ]);
      expect(second.tools?.map((t) => t.name)).toEqual(["read"]);
      expect(seenPolicy).toEqual([
        { tenant: "t1", round: 0 },
        { tenant: "t1", round: 1 },
      ]);

      const usage = await w.harness.session.document(usageDoc, conv.id);
      expect(usage.requests).toBe(2);
      expect(usage.byModel["fake/fake-1"]?.requests).toBe(2);
      expect(usage.total.outputTokens).toBeGreaterThan(0);
      expect(w.harness.runtime.tasks().every((t) => t.status === "terminal")).toBe(true);
      await w.crash();
    });

    it("AE1: a non-replay-safe tool cut off between intent and result yields interrupted", async () => {
      const fx = await backend.fixture();
      let executions = 0;
      const charge = defineTool({
        name: "charge",
        description: "Charge a card",
        parameters: z.object({ amount: z.number() }),
        execute: async (_args, api) => {
          executions++;
          api.output("charging step 1");
          return untilAborted(api.signal);
        },
      });
      fx.provider.enqueue(fakeToolCall("charge", { amount: 5 }, { id: "c1" }));
      const w1 = await fx.open({ tools: [charge] });
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["charge"] },
      });
      const sub = await conv.submit({ text: "charge me" });
      const toolTask = () => w1.harness.runtime.tasks().find((t) => t.kind === "agent.tool");
      await waitFor(async () => {
        const t = toolTask();
        if (!t) return false;
        const out = await w1.harness.session.document(toolOutputDoc, t.id);
        return out.text.includes("step 1");
      });
      expect(toolTask()?.phase).toBe("execute");
      await w1.crash();

      fx.provider.enqueue(fakeText("Sorry, it may have gone through."));
      const w2 = await fx.open({ tools: [charge] });
      const settled = await w2.harness.submission(sub.id).wait();
      expect(settled.status).toBe("done");
      const entries = await w2.harness.conversation(conv.id).entries();
      const [result] = results(entries);
      expect(result.status).toBe("interrupted");
      expect(result.isError).toBe(true);
      expect(result.content).toContain("may have happened");
      expect(result.content).toContain("charging step 1");
      expect(executions).toBe(1);
      await w2.crash();
    });

    it("AE2: a replay-safe tool reruns with the same idempotency key and the rerun's result is used", async () => {
      const fx = await backend.fixture();
      const keys: string[] = [];
      const fetchTool = defineTool({
        name: "fetch",
        description: "Fetch",
        parameters: z.object({ url: z.string() }),
        replay: "safe",
        execute: async (_args, api) => {
          keys.push(api.idempotencyKey);
          if (keys.length === 1) return untilAborted(api.signal);
          return { content: `second attempt ${api.attempt}` };
        },
      });
      fx.provider.enqueue(fakeToolCall("fetch", { url: "x" }, { id: "c1" }));
      const w1 = await fx.open({ tools: [fetchTool] });
      const conv = await w1.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["fetch"] },
      });
      const sub = await conv.submit({ text: "go" });
      await waitFor(() => keys.length === 1);
      await w1.crash();

      fx.provider.enqueue(fakeText("done"));
      const w2 = await fx.open({ tools: [fetchTool] });
      expect((await w2.harness.submission(sub.id).wait()).status).toBe("done");
      const [result] = results(await w2.harness.conversation(conv.id).entries());
      expect(result.status).toBe("ok");
      expect(result.content).toBe("second attempt 2");
      expect(keys).toHaveLength(2);
      expect(keys[0]).toBe(keys[1]);
      expect(keys[0]).toMatch(/:c1$/);
      await w2.crash();
    });

    it("a hook that rewrites allowed arguments into blocked ones is blocked by policy on the final arguments", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      const seen: Any[] = [];
      const policy: HostPolicy = {
        resolveModelRequest: allowAll.resolveModelRequest,
        authorizeTool: (call) => {
          seen.push(call.arguments);
          return String(call.arguments.path).startsWith("/etc")
            ? { decision: "block", reason: "system files are off limits" }
            : { decision: "allow" };
        },
      };
      const hooks: AgentHooks = {
        beforeTool: (e) => ({ arguments: { ...e.arguments, path: "/etc/passwd" } }),
      };
      fx.provider.enqueue(fakeToolCall("read", { path: "/tmp/ok" }, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({ tools: [readTool(log)], policy, hooks });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      expect((await (await conv.submit({ text: "x" })).wait()).status).toBe("done");
      const [result] = results(await conv.entries());
      expect(result.status).toBe("blocked");
      expect(result.content).toContain("system files are off limits");
      expect(seen).toEqual([{ path: "/etc/passwd" }]);
      expect(log).toEqual([]);
      await w.crash();
    });

    it("authorizeTool blocking (or throwing for) an enabled tool yields blocked", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      const policy: HostPolicy = {
        resolveModelRequest: allowAll.resolveModelRequest,
        authorizeTool: (call) => {
          if (call.toolCallId === "c1") return { decision: "block", reason: "nope" };
          throw new Error("policy service down");
        },
      };
      fx.provider.enqueue(twoCalls({ path: "a" }, { path: "b" }), fakeText("ok"));
      const w = await fx.open({ tools: [readTool(log)], policy });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      expect((await (await conv.submit({ text: "x" })).wait()).status).toBe("done");
      const rs = results(await conv.entries()).sort((a, b) =>
        a.toolCallId.localeCompare(b.toolCallId),
      );
      expect(rs.map((r) => r.status)).toEqual(["blocked", "blocked"]);
      expect(rs[1].content).toContain("policy service down");
      expect(log).toEqual([]);
      await w.crash();
    });

    it("invalid arguments yield invalid_arguments without executing", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      fx.provider.enqueue(fakeToolCall("read", { path: 42 }, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({ tools: [readTool(log)] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "x" })).wait();
      const [result] = results(await conv.entries());
      expect(result.status).toBe("invalid_arguments");
      expect(result.isError).toBe(true);
      expect(log).toEqual([]);
      await w.crash();
    });

    it("a tool the conversation does not enable yields unknown_tool", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      fx.provider.enqueue(fakeToolCall("read", { path: "a" }, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({ tools: [readTool(log)] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: [] },
      });
      await (await conv.submit({ text: "x" })).wait();
      expect(results(await conv.entries())[0].status).toBe("unknown_tool");
      expect(log).toEqual([]);
      await w.crash();
    });

    it("calls over the per-round limit get limit_exceeded", async () => {
      const fx = await backend.fixture();
      const log: string[] = [];
      fx.provider.enqueue(
        {
          steps: ["a", "b", "c"].map((p, i) => ({
            type: "tool_call" as const,
            id: `c${i + 1}`,
            name: "read",
            arguments: { path: p },
          })),
        },
        fakeText("ok"),
      );
      const w = await fx.open({ tools: [readTool(log)], limits: { maxToolCallsPerRound: 2 } });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "x" })).wait();
      const rs = results(await conv.entries());
      expect(rs.map((r) => [r.toolCallId, r.status]).sort()).toEqual([
        ["c1", "ok"],
        ["c2", "ok"],
        ["c3", "limit_exceeded"],
      ]);
      expect(log.sort()).toEqual(["read:a", "read:b"]);
      await w.crash();
    });

    it("does not re-append an unchanged prompt, and appends a changed one", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeText("one"), fakeText("two"), fakeText("three"));
      const w = await fx.open({ tools: [readTool()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "1" })).wait();
      await (await conv.submit({ text: "2" })).wait();
      expect(kinds(await conv.entries())).toEqual([
        "user",
        "system",
        "assistant",
        "user",
        "assistant",
      ]);
      await conv.configure({ systemPrompt: "You are terse." });
      await (await conv.submit({ text: "3" })).wait();
      const entries = await conv.entries();
      expect(kinds(entries).filter((k) => k === "system")).toHaveLength(2);
      const last = fx.provider.requests.at(-1) as ModelRequest;
      expect(last.messages[0]).toEqual({ role: "system", text: "You are terse." });
      expect(last.messages.filter((m) => m.role === "system")).toHaveLength(1);
      await w.crash();
    });

    it("a model error fails the run and settles the input unanswered(model_error)", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue(fakeError("overloaded"));
      const w = await fx.open({ tools: [readTool()] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const settled = await (await conv.submit({ text: "x" })).wait();
      expect(settled.status).toBe("unanswered");
      expect((settled.result as Any).reason).toBe("model_error");
      expect((settled.result as Any).error.kind).toBe("overloaded");
      expect(kinds(await conv.entries())).toEqual(["user", "system", "run_error"]);
      expect(w.harness.runtime.tasks().every((t) => t.status === "terminal")).toBe(true);
      await w.crash();
    });

    it("a model request denied by policy settles unanswered without calling the provider", async () => {
      const fx = await backend.fixture();
      const policy: HostPolicy = {
        resolveModelRequest: () => ({ ok: false, reason: "quota exhausted", code: "quota" }),
        authorizeTool: allowAll.authorizeTool,
      };
      const w = await fx.open({ tools: [readTool()], policy });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const settled = await (await conv.submit({ text: "x" })).wait();
      expect(settled.status).toBe("unanswered");
      expect((settled.result as Any).error.kind).toBe("denied");
      expect(fx.provider.requests).toHaveLength(0);
      await w.crash();
    });

    it("sequential tools never overlap with the rest of their round", async () => {
      const fx = await backend.fixture();
      const events: string[] = [];
      let active = 0;
      const mk = (name: string, sequential: boolean) =>
        defineTool({
          name,
          description: name,
          parameters: z.object({}),
          sequential,
          execute: async () => {
            active++;
            events.push(`${name}:start:${active}`);
            await new Promise((r) => setTimeout(r, 10));
            events.push(`${name}:end`);
            active--;
            return { content: name };
          },
        });
      fx.provider.enqueue(
        {
          steps: [
            { type: "tool_call", id: "c1", name: "p1", arguments: {} },
            { type: "tool_call", id: "c2", name: "s", arguments: {} },
            { type: "tool_call", id: "c3", name: "p2", arguments: {} },
          ],
        },
        fakeText("ok"),
      );
      const w = await fx.open({ tools: [mk("p1", false), mk("s", true), mk("p2", false)] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["p1", "s", "p2"] },
      });
      await (await conv.submit({ text: "x" })).wait();
      expect(events).toEqual([
        "p1:start:1",
        "p1:end",
        "s:start:1",
        "s:end",
        "p2:start:1",
        "p2:end",
      ]);
      await w.crash();
    });

    it("a round where every result says terminate ends the run without another request", async () => {
      const fx = await backend.fixture();
      const finish = defineTool({
        name: "finish",
        description: "Finish",
        parameters: z.object({ answer: z.string() }),
        execute: async (args) => ({ content: args.answer, terminate: true }),
      });
      fx.provider.enqueue(fakeToolCall("finish", { answer: "42" }, { id: "c1" }));
      const w = await fx.open({ tools: [finish] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["finish"] },
      });
      const settled = await (await conv.submit({ text: "x" })).wait();
      expect(settled.status).toBe("done");
      expect((settled.result as Any).terminated).toBe(true);
      expect(fx.provider.requests).toHaveLength(1);
      await w.crash();
    });

    it("throwing generic hooks are reported and skipped; afterTool may rewrite the result", async () => {
      const fx = await backend.fixture();
      const errors: Any[] = [];
      const hooks: AgentHooks[] = [
        {
          beforeRequest: () => {
            throw new Error("bad hook");
          },
          beforeTool: () => {
            throw new Error("bad tool hook");
          },
        },
        {
          afterTool: (e) => ({ result: { ...e.result, content: `[checked] ${e.result.content}` } }),
        },
      ];
      fx.provider.enqueue(fakeToolCall("read", { path: "a" }, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({
        tools: [readTool()],
        hooks,
        onError: (err, info) => errors.push([info.during, (err as Error).message]),
      });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      expect((await (await conv.submit({ text: "x" })).wait()).status).toBe("done");
      expect(results(await conv.entries())[0].content).toBe("[checked] contents of a");
      expect(errors).toContainEqual(["hook:beforeRequest", "bad hook"]);
      expect(errors).toContainEqual(["hook:beforeTool", "bad tool hook"]);
      await w.crash();
    });

    it("streams throttled partial output to the live document", async () => {
      const fx = await backend.fixture();
      fx.provider.enqueue({
        steps: [{ type: "text", text: "abcdefghijklmnopqrst", chunkSize: 1 }],
        chunkDelayMs: 10,
      });
      const w = await fx.open({ tools: [] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: [] },
      });
      let liveWrites = 0;
      w.harness.session.subscribeCommits((e) => {
        for (const d of e.documents) if (d.kind === "agent.live") liveWrites++;
      });
      const sub = await conv.submit({ text: "x" });
      let settled = false;
      const waited = sub.wait().then(() => {
        settled = true;
      });
      // Move the clock in small steps, giving storage real time between them.
      for (let i = 0; i < 2_000 && !settled; i++) {
        await fx.clock.advanceAsync(5);
        await new Promise((r) => setTimeout(r, 1));
      }
      await waited;
      expect(settled).toBe(true);
      // 20 deltas over ~200ms of clock: at most one write per 100ms (+ start and final clear).
      expect(liveWrites).toBeGreaterThan(1);
      expect(liveWrites).toBeLessThanOrEqual(5);
      const live = await w.harness.conversation(conv.id).live();
      expect(live.message).toBeNull();
      await w.crash();
    });

    it("redacts tool arguments and results before commit", async () => {
      const fx = await backend.fixture();
      const scrub = (v: JsonValue): JsonValue =>
        JSON.parse(JSON.stringify(v).replaceAll("hunter2", "[redacted]"));
      fx.provider.enqueue(fakeToolCall("read", { path: "hunter2" }, { id: "c1" }), fakeText("ok"));
      const w = await fx.open({ tools: [readTool()], redact: scrub });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "x" })).wait();
      const all = JSON.stringify(await conv.entries());
      expect(all).not.toContain("hunter2");
      expect(all).toContain("contents of [redacted]");
      await w.crash();
    });

    it("aborting a run mid-tool settles the input unanswered(aborted) and leaves nothing running", async () => {
      const fx = await backend.fixture();
      const block = defineTool({
        name: "block",
        description: "Blocks",
        parameters: z.object({}),
        execute: async (_a, api) => untilAborted(api.signal),
      });
      fx.provider.enqueue(fakeToolCall("block", {}, { id: "c1" }), fakeText("after abort"));
      const w = await fx.open({ tools: [block] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["block"] },
      });
      const sub = await conv.submit({ text: "x" });
      await waitFor(() =>
        w.harness.runtime.tasks().some((t) => t.kind === "agent.tool" && t.phase === "execute"),
      );
      await w.harness.runtime.abortConversation(conv.id, "user_abort");
      const settled = await sub.wait();
      expect(settled.status).toBe("unanswered");
      expect((settled.result as Any).reason).toBe("aborted");
      await w.harness.waitForIdle();
      expect(w.harness.runtime.tasks().every((t) => t.status === "terminal")).toBe(true);
      expect(conv.busy()).toBe(false);
      // The next input sees a well-formed context: the dangling call gets an aborted result.
      expect((await (await conv.submit({ text: "again" })).wait()).status).toBe("done");
      const last = fx.provider.requests.at(-1) as ModelRequest;
      expect(last.messages.map((m) => m.role)).toEqual([
        "system",
        "user",
        "assistant",
        "tool_result",
        "user",
      ]);
      await w.crash();
    });

    it("rejects a submit with whenBusy reject while a run is active", async () => {
      const fx = await backend.fixture();
      const block = defineTool({
        name: "block",
        description: "Blocks",
        parameters: z.object({}),
        execute: async (_a, api) => untilAborted(api.signal),
      });
      fx.provider.enqueue(fakeToolCall("block", {}, { id: "c1" }));
      const w = await fx.open({ tools: [block] });
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: { systemPrompt: "s", tools: ["block"] },
      });
      await conv.submit({ text: "x" });
      await expect(conv.submit({ text: "y", whenBusy: "reject" })).rejects.toMatchObject({
        code: "conversation_busy",
      });
      await w.crash();
    });
  });
}
