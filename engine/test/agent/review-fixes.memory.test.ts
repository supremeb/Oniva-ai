/**
 * Review fixes: document redaction through the agent loop, interrupted
 * results for tools that faulted after their intent, result capping and the
 * live tool output cap.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { approvalsDoc } from "../../src/agent/approvals.js";
import type { HostPolicy } from "../../src/agent/policy.js";
import {
  appendOutput,
  LARGE_TOOL_OUTPUT_INTERVAL_MS,
  MAX_TOOL_OUTPUT_CHARS,
  toolOutputDoc,
  toolOutputIntervalMs,
} from "../../src/agent/progress.js";
import {
  capToolResult,
  INTERRUPTED_WARNING,
  TRUNCATED_RESULT_MARKER,
} from "../../src/agent/tool-task.js";
import { type JsonValue, jsonByteLength } from "../../src/core/json.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import type { CommitEvent } from "../../src/session/publish.js";
import type { Redact } from "../../src/session/transaction.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { allowAll, memoryBackend, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to entry payloads
type Any = any;
const results = (entries: EntryRecord[]): Any[] =>
  entries.filter((e) => e.kind === "tool_result").map((e) => e.payload);

const SECRET = "sk-livesecret0987654321";
const mask: Redact = (v: JsonValue) =>
  JSON.parse(JSON.stringify(v).replaceAll(SECRET, "[REDACTED]"));

describe("document redaction through the agent loop", () => {
  it("masks tokens in approval arguments, streamed tool output and the live message", async () => {
    const fx = await memoryBackend.fixture();
    const policy: HostPolicy = {
      resolveModelRequest: allowAll.resolveModelRequest,
      authorizeTool: () => ({ decision: "requireApproval", reason: "needs a human" }),
    };
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const deploy = defineTool({
      name: "deploy",
      description: "Deploy",
      parameters: z.object({ key: z.string() }),
      execute: async (_args, api) => {
        // The tool's own output carries a credential.
        api.output(`using ${SECRET}\n`);
        await gate;
        return { content: "deployed" };
      },
    });
    // The model never sees the secret; a host hook injects it into the
    // arguments, so only the approval document can leak it.
    fx.provider.enqueue(fakeToolCall("deploy", { key: "placeholder" }, { id: "c1" }), {
      steps: [{ type: "text", text: `${SECRET} is what the tool printed.`, chunkSize: 24 }],
    });
    const w = await fx.open({
      tools: [deploy],
      policy,
      hooks: { beforeTool: () => ({ arguments: { key: SECRET } }) },
      redact: mask,
      settings: { progressIntervalMs: 0 },
    });
    const events: CommitEvent[] = [];
    w.harness.session.subscribeCommits((e) => events.push(e));
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["deploy"] },
    });
    const sub = await conv.submit({ text: "deploy" });

    await waitFor(async () => (await w.harness.approvals(conv.id)).length > 0);
    const [item] = await w.harness.approvals(conv.id);
    expect(JSON.stringify(item)).not.toContain(SECRET);
    expect(JSON.stringify(item)).toContain("[REDACTED]");
    await w.harness.decideApproval({
      conversationId: conv.id,
      toolTaskId: item?.toolTaskId as string,
      decision: "approve",
      principal: "user:alice",
    });
    const taskId = item?.toolTaskId as string;
    await waitFor(async () =>
      (await w.harness.session.document(toolOutputDoc, taskId)).text.includes("using"),
    );
    expect((await w.harness.session.document(toolOutputDoc, taskId)).text).toBe(
      "using [REDACTED]\n",
    );
    release();
    expect((await sub.wait()).status).toBe("done");

    // Published events never carried the secret, including the live message.
    expect(events.some((e) => e.documents.some((d) => d.kind === "agent.live"))).toBe(true);
    expect(events.some((e) => e.documents.some((d) => d.kind === "agent.tool_output"))).toBe(true);
    expect(JSON.stringify(events)).not.toContain(SECRET);
    await w.crash();

    // And neither did storage: a fresh session reads what was stored.
    const w2 = await fx.open({ tools: [deploy] });
    const out = await w2.harness.session.document(toolOutputDoc, taskId);
    expect(out.text).toBe("using [REDACTED]\n");
    const approvals = await w2.harness.session.document(approvalsDoc, conv.id);
    expect(JSON.stringify(approvals)).not.toContain(SECRET);
    expect(JSON.stringify(await w2.harness.conversation(conv.id).entries())).not.toContain(SECRET);
    await w2.crash();
  });
});

describe("tool results that cannot commit", () => {
  it("a tool task that faulted in execute yields interrupted, not a plain failure", async () => {
    const fx = await memoryBackend.fixture();
    let executions = 0;
    const charge = defineTool({
      name: "charge",
      description: "Charge",
      parameters: z.object({ amount: z.number() }),
      execute: async (_args, api) => {
        executions++;
        api.output("charged step 1");
        return { content: "charged" };
      },
    });
    // The first tool_result commit fails, after the tool's effect happened.
    let failed = false;
    const redact: Redact = (v, t) => {
      if (!failed && t.record === "entry" && t.kind === "tool_result") {
        failed = true;
        throw new Error("result store unavailable");
      }
      return v;
    };
    fx.provider.enqueue(fakeToolCall("charge", { amount: 5 }, { id: "c1" }), fakeText("ok"));
    const w = await fx.open({ tools: [charge], redact, settings: { progressIntervalMs: 0 } });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["charge"] },
    });
    const settled = await (await conv.submit({ text: "charge" })).wait();
    expect(settled.status).toBe("done");
    const [result] = results(await conv.entries());
    expect(failed).toBe(true);
    expect(executions).toBe(1);
    expect(result.status).toBe("interrupted");
    expect(result.isError).toBe(true);
    expect(result.content).toContain(INTERRUPTED_WARNING);
    expect(result.content).toContain("charged step 1");
    expect(result.details.warning).toBe(INTERRUPTED_WARNING);
    await w.crash();
  });

  it("an oversized tool result is truncated to fit the entry limit and commits", async () => {
    const fx = await memoryBackend.fixture();
    const big = defineTool({
      name: "big",
      description: "Big",
      parameters: z.object({}),
      execute: async () => ({ content: "x".repeat(50_000), details: { blob: "y".repeat(50_000) } }),
    });
    fx.provider.enqueue(fakeToolCall("big", {}, { id: "c1" }), fakeText("ok"));
    const w = await fx.open({ tools: [big], limits: { maxEntryBytes: 8192 } });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["big"] },
    });
    const settled = await (await conv.submit({ text: "go" })).wait();
    expect(settled.status).toBe("done");
    const entries = await conv.entries();
    const entry = entries.find((e) => e.kind === "tool_result") as EntryRecord;
    const [result] = results(entries);
    expect(result.status).toBe("ok");
    expect(result.content).toContain(TRUNCATED_RESULT_MARKER);
    expect(result.content.startsWith("xxxx")).toBe(true);
    expect(jsonByteLength(entry)).toBeLessThanOrEqual(8192);
    await w.crash();
  });

  it("capToolResult leaves fitting results alone and fits escape-heavy content", () => {
    const call = { id: "c1", name: "t" };
    const small = { content: "fine" };
    expect(capToolResult(call, small, 8192)).toBe(small);
    const heavy = capToolResult(
      call,
      { content: [{ type: "text", text: '"\u0001'.repeat(20_000) }], isError: true },
      8192,
    );
    expect(typeof heavy.content).toBe("string");
    expect(heavy.content as string).toContain(TRUNCATED_RESULT_MARKER);
    expect(heavy.isError).toBe(true);
    expect(jsonByteLength(heavy)).toBeLessThanOrEqual(8192 - 1024);
  });
});

describe("live tool output cost", () => {
  it("keeps only a 16K tail in the live document while the result stays whole", async () => {
    expect(MAX_TOOL_OUTPUT_CHARS).toBe(16 * 1024);
    const fx = await memoryBackend.fixture();
    const chunk = "z".repeat(1000);
    const noisy = defineTool({
      name: "noisy",
      description: "Noisy",
      parameters: z.object({}),
      execute: async (_args, api) => {
        for (let i = 0; i < 40; i++) api.output(chunk);
        api.output("END");
        return { content: `${chunk.repeat(40)}END` };
      },
    });
    fx.provider.enqueue(fakeToolCall("noisy", {}, { id: "c1" }), fakeText("ok"));
    const w = await fx.open({ tools: [noisy], settings: { progressIntervalMs: 0 } });
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["noisy"] },
    });
    await (await conv.submit({ text: "go" })).wait();
    const task = w.harness.runtime.tasks().find((t) => t.kind === "agent.tool");
    const out = await w.harness.session.document(toolOutputDoc, task?.id as string);
    expect(out.text.length).toBe(MAX_TOOL_OUTPUT_CHARS);
    expect(out.text.endsWith("END")).toBe(true);
    expect(out.truncated).toBe(true);
    const [result] = results(await conv.entries());
    expect(result.content.length).toBe(40_003);
    await w.crash();
  });

  it("writes large snapshots less often, and keeps throttling off when disabled", () => {
    expect(toolOutputIntervalMs(100, 1000)).toBe(100);
    expect(toolOutputIntervalMs(100, 9000)).toBe(LARGE_TOOL_OUTPUT_INTERVAL_MS);
    expect(toolOutputIntervalMs(500, 9000)).toBe(500);
    expect(toolOutputIntervalMs(0, 9000)).toBe(0);
    const s = appendOutput(toolOutputDoc.initial, "a".repeat(20_000));
    expect(s.text.length).toBe(MAX_TOOL_OUTPUT_CHARS);
    expect(s.truncated).toBe(true);
  });
});
