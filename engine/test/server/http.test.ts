import { request as httpRequest } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import { z } from "zod";
import type { HostPolicy } from "../../src/agent/policy.js";
import { createSubagentTool } from "../../src/agent/subagents.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { defineTool } from "../../src/tools/registry.js";
import type { WatchFrame } from "../../src/watch/stream.js";
import { loadConversationView } from "../../src/watch/view.js";
import { type AgentWorker, allowAll, memoryBackend, waitFor } from "../agent/support.js";
import { ClientView, gate, gatedTool } from "../watch/support.js";
import { AUTH, call, SseClient, serve, TENANT, TOKEN } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  for (const fn of cleanups.splice(0).reverse()) await fn();
});

async function start(
  workerOptions: Parameters<Awaited<ReturnType<typeof memoryBackend.fixture>>["open"]>[0] = {},
  serverOptions: Parameters<typeof serve>[1] = {},
) {
  const fx = await memoryBackend.fixture();
  const w = await fx.open({ settings: { progressIntervalMs: 0 }, ...workerOptions });
  const srv = await serve(w.harness, serverOptions);
  cleanups.push(() => w.crash());
  cleanups.push(() => srv.close());
  return { fx, w, srv, url: srv.url };
}

async function createConv(url: string, body: unknown = {}): Promise<string> {
  const res = await call(url, "POST", "/v1/conversations", { headers: AUTH, body });
  expect(res.status).toBe(201);
  return res.body.conversationId as string;
}

const approvalPolicy: HostPolicy = {
  resolveModelRequest: allowAll.resolveModelRequest,
  authorizeTool: () => ({ decision: "requireApproval", reason: "needs a human" }),
};

function deployTool(log: string[]) {
  return defineTool({
    name: "deploy",
    description: "Deploy",
    parameters: z.object({ service: z.string() }),
    execute: async (args) => {
      log.push(args.service);
      return { content: `deployed ${args.service}` };
    },
  });
}

async function pendingApproval(w: AgentWorker, conversationId: string) {
  await waitFor(async () =>
    (await w.harness.approvals(conversationId)).some((i) => i.status === "pending"),
  );
  await w.harness.waitForIdle();
  return (await w.harness.approvals(conversationId)).find((i) => i.status === "pending") as Any;
}

/** The child conversation a parent's still-running `research` subagent call created. */
function childApprovalOwner(w: AgentWorker, parentId: string): string | undefined {
  const tool = w.harness.runtime
    .tasks({ conversationId: parentId })
    .find((t) => t.kind === "agent.tool" && (t.input as Any)?.call?.name === "research");
  return (tool?.checkpoint as Any)?.subagent?.conversationId;
}

describe("engine HTTP server", () => {
  it("serves health without auth", async () => {
    const { url, w } = await start();
    const res = await call(url, "GET", "/v1/health");
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({
      status: "ok",
      sessionId: w.harness.session.sessionId,
      ownerId: "worker-a",
    });
  });

  it("creates conversations bound to the tenant and project headers", async () => {
    const { url, w } = await start();
    const id = await createConv(url, { agent: { systemPrompt: "hi" } });
    const rec = await w.harness.conversation(id).info();
    expect(rec).toMatchObject({ tenantId: "t1", projectId: "p1" });
    expect((await w.harness.conversation(id).agent()).systemPrompt).toBe("hi");

    const missing = await call(url, "POST", "/v1/conversations", {
      headers: { authorization: `Bearer ${TOKEN}`, "x-oniva-tenant": "t1" },
      body: {},
    });
    expect(missing.status).toBe(400);
    expect(missing.body.error).toBe("missing_header");
    const dup = await call(url, "POST", "/v1/conversations", { headers: AUTH, body: { id } });
    expect(dup.status).toBe(409);
  });

  it("rejects a missing or wrong token with 401 on every route but health", async () => {
    const { url } = await start();
    const id = await createConv(url);
    const routes: Array<[string, string]> = [
      ["POST", "/v1/conversations"],
      ["POST", `/v1/conversations/${id}/submissions`],
      ["GET", `/v1/conversations/${id}/watch`],
      ["POST", `/v1/conversations/${id}/abort`],
      ["GET", `/v1/conversations/${id}/approvals`],
      ["POST", `/v1/conversations/${id}/approvals/task_x/decision`],
    ];
    for (const [method, path] of routes) {
      const body = method === "POST" ? { body: {} } : {};
      const none = await call(url, method, path, { headers: TENANT, ...body });
      expect(none.status, `${method} ${path}`).toBe(401);
      expect(none.body.error).toBe("unauthorized");
      const wrong = await call(url, method, path, {
        headers: { ...TENANT, authorization: `Bearer ${TOKEN}x` },
        ...body,
      });
      expect(wrong.status).toBe(401);
      const short = await call(url, method, path, {
        headers: { ...TENANT, authorization: "Bearer x" },
        ...body,
      });
      expect(short.status).toBe(401);
    }
  });

  it("rejects a tenant or project mismatch with 403, including watch and decision", async () => {
    const { url } = await start();
    const id = await createConv(url);
    const others = [
      { ...AUTH, "x-oniva-tenant": "t2" },
      { ...AUTH, "x-oniva-project": "p2" },
    ];
    for (const headers of others) {
      const routes: Array<[string, string, Record<string, string>]> = [
        ["POST", `/v1/conversations/${id}/submissions`, headers],
        ["GET", `/v1/conversations/${id}/watch`, headers],
        ["POST", `/v1/conversations/${id}/abort`, headers],
        ["GET", `/v1/conversations/${id}/approvals`, headers],
        [
          "POST",
          `/v1/conversations/${id}/approvals/task_x/decision`,
          { ...headers, "x-oniva-principal": "user:alice" },
        ],
      ];
      for (const [method, path, h] of routes) {
        const res = await call(url, method, path, {
          headers: h,
          ...(method === "POST" ? { body: { text: "x", decision: "approve" } } : {}),
        });
        expect(res.status, `${method} ${path}`).toBe(403);
        expect(res.body.error).toBe("forbidden");
      }
    }
  });

  it("returns 404 for an unknown conversation or route", async () => {
    const { url } = await start();
    const res = await call(url, "POST", "/v1/conversations/conv_missing/submissions", {
      headers: AUTH,
      body: { text: "x" },
    });
    expect(res.status).toBe(404);
    expect(res.body.error).toBe("not_found");
    const watch = await call(url, "GET", "/v1/conversations/conv_missing/watch", {
      headers: AUTH,
    });
    expect(watch.status).toBe(404);
    const approvals = await call(url, "GET", "/v1/conversations/conv_missing/approvals", {
      headers: AUTH,
    });
    expect(approvals.status).toBe(404);
    expect((await call(url, "GET", "/v1/nope", { headers: AUTH })).status).toBe(404);
    const wrongMethod = await call(url, "GET", "/v1/conversations", { headers: AUTH });
    expect(wrongMethod.status).toBe(405);
  });

  it("answers 409 with the owner id when this worker does not own the session", async () => {
    let owner = true;
    const { url } = await start({}, { isOwner: () => owner, currentOwner: async () => "worker-b" });
    const id = await createConv(url);
    owner = false;
    const res = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "x" },
    });
    expect(res.status).toBe(409);
    expect(res.body).toEqual({ error: "not_owner", ownerId: "worker-b" });
    const watch = await call(url, "GET", `/v1/conversations/${id}/watch`, { headers: AUTH });
    expect(watch.status).toBe(409);
    const health = await call(url, "GET", "/v1/health");
    expect(health.body.owner).toBe(false);
  });

  it("rejects an oversized body with 413 and malformed input with 400", async () => {
    const { url, w } = await start({}, { limits: { maxRequestBodyBytes: 512 } });
    const id = await createConv(url);
    const big = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "x".repeat(2048) },
    });
    expect(big.status).toBe(413);
    expect(big.body.error).toBe("payload_too_large");

    const badJson = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      raw: "{nope",
    });
    expect(badJson.status).toBe(400);
    expect(badJson.body.error).toBe("bad_request");
    const notObject = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: [1],
    });
    expect(notObject.status).toBe(400);
    const badBusy = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "x", whenBusy: "sometimes" },
    });
    expect(badBusy.status).toBe(400);
    const noContent = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: {},
    });
    expect(noContent.status).toBe(400);
    const badUntrusted = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "x", untrusted: "yes" },
    });
    expect(badUntrusted.status).toBe(400);
    expect(badUntrusted.body).toMatchObject({
      error: "bad_request",
      reason: "untrusted must be a boolean",
    });
    expect(await w.harness.conversation(id).entries()).toHaveLength(0);
  });

  it("413s a chunked body that grows past the limit", async () => {
    const { srv } = await start({}, { limits: { maxRequestBodyBytes: 256 } });
    const { port } = new URL(srv.url);
    const status = await new Promise<number>((resolve, reject) => {
      const req = httpRequest(
        {
          host: "127.0.0.1",
          port: Number(port),
          method: "POST",
          path: "/v1/conversations",
          headers: { ...AUTH, "content-type": "application/json", "transfer-encoding": "chunked" },
        },
        (res) => {
          res.resume();
          resolve(res.statusCode ?? 0);
        },
      );
      req.on("error", reject);
      req.write(`{"agent":{"systemPrompt":"${"y".repeat(200)}`);
      req.end(`${"y".repeat(200)}"}}`);
    });
    expect(status).toBe(413);
  });

  it("returns the same submission id for a duplicate submit", async () => {
    const { url, w } = await start();
    const id = await createConv(url);
    const body = { text: "hello", requestId: "req-1", type: "write" };
    const a = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body,
    });
    const b = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body,
    });
    expect(a.status).toBe(202);
    expect(b.status).toBe(202);
    expect(a.body.submissionId).toMatch(/^sub/);
    expect(b.body.submissionId).toBe(a.body.submissionId);
    expect(b.body).toMatchObject({ requestId: "req-1", status: "done" });
    expect(await w.harness.conversation(id).entries()).toHaveLength(1);
  });

  it("answers 409 for a busy conversation with whenBusy reject, and aborts", async () => {
    const gates: [ReturnType<typeof gate>, ReturnType<typeof gate>] = [gate(), gate()];
    const { url, w, fx } = await start({ tools: [gatedTool(gates)] });
    fx.provider.enqueue(fakeToolCall("work", {}, { id: "c1" }));
    const id = await createConv(url, { agent: { systemPrompt: "s", tools: ["work"] } });
    const first = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "go" },
    });
    expect(first.status).toBe(202);
    await waitFor(() => w.harness.runtime.tasks().some((t) => t.kind === "agent.tool"));
    const busy = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "again", whenBusy: "reject" },
    });
    expect(busy.status).toBe(409);
    expect(busy.body.error).toBe("conversation_busy");

    const aborted = await call(url, "POST", `/v1/conversations/${id}/abort`, {
      headers: AUTH,
      body: { reason: "user stop" },
    });
    expect(aborted.status).toBe(200);
    expect(w.harness.conversation(id).busy()).toBe(false);
    const settled = await w.harness.submission(first.body.submissionId).wait();
    expect(settled.status).toBe("unanswered");
    gates[0].open();
    gates[1].open();
  });

  it("cancels a queued submission over DELETE, 409 once settled, 404 for a stranger", async () => {
    const gates: [ReturnType<typeof gate>, ReturnType<typeof gate>] = [gate(), gate()];
    const { url, w, fx } = await start({ tools: [gatedTool(gates)] });
    fx.provider.enqueue(fakeToolCall("work", {}, { id: "c1" }));
    const id = await createConv(url, { agent: { systemPrompt: "s", tools: ["work"] } });
    const first = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "go" },
    });
    await waitFor(() => w.harness.runtime.tasks().some((t) => t.kind === "agent.tool"));
    const queued = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "later", requestId: "rq-1" },
    });
    expect(queued.status).toBe(202);
    const path = (sid: string) => `/v1/conversations/${id}/submissions/${sid}`;

    const noAuth = await call(url, "DELETE", path(queued.body.submissionId), { headers: TENANT });
    expect(noAuth.status).toBe(401);
    const wrongTenant = await call(url, "DELETE", path(queued.body.submissionId), {
      headers: { ...AUTH, "x-oniva-tenant": "t2" },
    });
    expect(wrongTenant.status).toBe(403);

    const del = await call(url, "DELETE", path(queued.body.submissionId), { headers: AUTH });
    expect(del.status).toBe(200);
    expect(del.body).toMatchObject({ submissionId: queued.body.submissionId, cancelled: true });
    const settled = await w.harness.submission(queued.body.submissionId).wait();
    expect(settled.status).toBe("unanswered");
    const again = await call(url, "DELETE", path(queued.body.submissionId), { headers: AUTH });
    expect(again.status).toBe(409);
    expect(again.body.error).toBe("not_queued");
    const running = await call(url, "DELETE", path(first.body.submissionId), { headers: AUTH });
    expect(running.status).toBe(409);
    const missing = await call(url, "DELETE", path("subm_nope"), { headers: AUTH });
    expect(missing.status).toBe(404);
    gates[0].open();
    gates[1].open();
  });

  it("requires a principal for decisions, and a valid decision wakes the tool", async () => {
    const log: string[] = [];
    const { url, w, fx } = await start({ tools: [deployTool(log)], policy: approvalPolicy });
    fx.provider.enqueue(fakeToolCall("deploy", { service: "api" }, { id: "c1" }), fakeText("ok"));
    const id = await createConv(url, { agent: { systemPrompt: "s", tools: ["deploy"] } });
    const sub = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "deploy" },
    });
    const item = await pendingApproval(w, id);
    const path = `/v1/conversations/${id}/approvals/${item.toolTaskId}/decision`;

    const noPrincipal = await call(url, "POST", path, {
      headers: AUTH,
      body: { decision: "approve" },
    });
    expect(noPrincipal.status).toBe(400);
    expect(noPrincipal.body.error).toBe("missing_header");
    const invalid = await call(url, "POST", path, {
      headers: { ...AUTH, "x-oniva-principal": "user:alice" },
      body: { decision: "maybe" },
    });
    expect(invalid.status).toBe(400);
    expect(invalid.body).toMatchObject({ error: "approval_rejected", reason: "invalid" });
    const otherTask = await call(
      url,
      "POST",
      `/v1/conversations/${id}/approvals/task_nope/decision`,
      {
        headers: { ...AUTH, "x-oniva-principal": "user:alice" },
        body: { decision: "approve" },
      },
    );
    expect(otherTask.status).toBe(409);
    expect(otherTask.body).toMatchObject({ error: "approval_rejected", reason: "not_owned" });
    expect(log).toEqual([]);

    const ok = await call(url, "POST", path, {
      headers: { ...AUTH, "x-oniva-principal": "user:alice" },
      body: { decision: "approve", note: "go" },
    });
    expect(ok.status).toBe(200);
    expect(ok.body.approval).toMatchObject({
      status: "approved",
      decision: { decision: "approve", principal: "user:alice", note: "go" },
    });
    const settled = await w.harness.submission(sub.body.submissionId).wait();
    expect(settled.status).toBe("done");
    expect(log).toEqual(["api"]);

    const again = await call(url, "POST", path, {
      headers: { ...AUTH, "x-oniva-principal": "user:alice" },
      body: { decision: "deny" },
    });
    expect(again.status).toBe(409);
    expect(again.body.reason).toBe("already_decided");
    expect(again.body.approval).toMatchObject({ id: item.id, status: "approved" });
  });

  it("lists a conversation's approval items and reaches a subagent child's item through the root", async () => {
    const childPolicy: HostPolicy = {
      resolveModelRequest: allowAll.resolveModelRequest,
      authorizeTool: ({ toolName }) =>
        toolName === "scrape"
          ? { decision: "requireApproval", reason: "needs a human" }
          : { decision: "allow" },
    };
    const research = createSubagentTool({ name: "research", tools: ["scrape"] });
    const scrape = defineTool({
      name: "scrape",
      description: "Scrape",
      parameters: z.object({}),
      execute: () => ({ content: "scraped" }),
    });
    const { url, w, fx } = await start({ tools: [research, scrape], policy: childPolicy });
    fx.provider.enqueue(
      fakeToolCall("research", { prompt: "go" }, { id: "r1" }),
      fakeToolCall("scrape", {}, { id: "s1" }),
      fakeText("scraped"),
      fakeText("done"),
    );
    const id = await createConv(url, { agent: { systemPrompt: "s", tools: ["research"] } });
    const sub = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "research" },
    });

    await waitFor(() => childApprovalOwner(w, id) !== undefined);
    const child = childApprovalOwner(w, id) as string;
    await w.harness.waitForIdle();
    const item = (await w.harness.approvals(child)).find((i) => i.status === "pending") as Any;

    const view = await call(url, "GET", `/v1/conversations/${id}/approvals`, { headers: AUTH });
    expect(view.status).toBe(200);
    expect(view.body.conversationId).toBe(id);
    expect(view.body.approvals).toHaveLength(1);
    expect(child).not.toBe(id);
    expect(view.body.approvals[0]).toMatchObject({
      id: item.id,
      toolName: "scrape",
      status: "pending",
    });

    const decided = await call(
      url,
      "POST",
      `/v1/conversations/${id}/approvals/${item.toolTaskId}/decision`,
      { headers: { ...AUTH, "x-oniva-principal": "user:alice" }, body: { decision: "approve" } },
    );
    expect(decided.status).toBe(200);
    expect(decided.body.approval).toMatchObject({ id: item.id, status: "approved" });
    const settled = await w.harness.submission(sub.body.submissionId).wait();
    expect(settled.status).toBe("done");
  });

  it("streams a mid-run watch over SSE: snapshot, then patches that reproduce the final view", async () => {
    const gates: [ReturnType<typeof gate>, ReturnType<typeof gate>] = [gate(), gate()];
    const { url, w, fx } = await start({ tools: [gatedTool(gates)] });
    fx.provider.enqueue(fakeToolCall("work", {}, { id: "c1", text: "On it" }), {
      steps: [{ type: "text", text: "Finished the work now.", chunkSize: 5 }],
    });
    const id = await createConv(url, { agent: { systemPrompt: "s", tools: ["work"] } });
    const sub = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
      headers: AUTH,
      body: { text: "go" },
    });
    await waitFor(async () => {
      const v = await loadConversationView(w.harness.session, id);
      return Object.keys(v.toolOutput).length > 0;
    });

    const sse = await SseClient.open(`${url}/v1/conversations/${id}/watch`, AUTH);
    expect(sse.status).toBe(200);
    expect(sse.headers.get("content-type")).toContain("text/event-stream");
    const client = new ClientView();
    const first = await sse.next();
    expect(first.event).toBe("snapshot");
    const firstFrame = JSON.parse(first.data) as WatchFrame;
    expect(first.id).toBe(String(firstFrame.cursor));
    client.apply(firstFrame);

    gates[0].open();
    gates[1].open();
    await w.harness.submission(sub.body.submissionId).wait();
    await w.harness.waitForIdle();
    const final = await loadConversationView(w.harness.session, id);
    while (JSON.stringify(client.view) !== JSON.stringify(final)) {
      const e = await sse.next();
      expect(e.event).toBe("patch");
      const frame = JSON.parse(e.data) as WatchFrame;
      expect(e.id).toBe(String(frame.cursor));
      client.apply(frame);
    }
    expect(client.view).toEqual(final);
    expect(client.frames.length).toBeGreaterThan(2);
    sse.close();
  });

  it("honors Last-Event-ID and ?cursor= inside and outside the window", async () => {
    const { url } = await start({}, { watch: { retainFrames: 4 } });
    const id = await createConv(url);
    const write = (text: string) =>
      call(url, "POST", `/v1/conversations/${id}/submissions`, {
        headers: AUTH,
        body: { text, type: "write" },
      });
    const sse = await SseClient.open(`${url}/v1/conversations/${id}/watch`, AUTH);
    const snap = await sse.frame();
    await write("a");
    const a = await sse.frame();
    sse.close();
    await write("b");
    await write("c");

    const resumed = await SseClient.open(`${url}/v1/conversations/${id}/watch`, {
      ...AUTH,
      "last-event-id": String(a.frame.cursor),
    });
    const missed = await resumed.frames();
    expect(missed).toHaveLength(2);
    expect(missed.every((f) => "patch" in f)).toBe(true);
    resumed.close();

    const byQuery = await SseClient.open(
      `${url}/v1/conversations/${id}/watch?cursor=${a.frame.cursor}`,
      AUTH,
    );
    expect(await byQuery.frames()).toHaveLength(2);
    byQuery.close();

    for (const t of ["d", "e", "f", "g"]) await write(t);
    const stale = await SseClient.open(`${url}/v1/conversations/${id}/watch`, {
      ...AUTH,
      "last-event-id": String(snap.frame.cursor),
    });
    const frames = await stale.frames();
    expect(frames).toHaveLength(1);
    expect("snapshot" in (frames[0] as WatchFrame)).toBe(true);
    stale.close();

    const bad = await call(url, "GET", `/v1/conversations/${id}/watch?cursor=abc`, {
      headers: AUTH,
    });
    expect(bad.status).toBe(400);
  });

  it("a paused SSE consumer never blocks commits", async () => {
    const { url, srv } = await start();
    const id = await createConv(url);
    const { port } = new URL(srv.url);
    const req = httpRequest({
      host: "127.0.0.1",
      port: Number(port),
      path: `/v1/conversations/${id}/watch`,
      headers: AUTH,
    });
    const paused = new Promise<void>((resolve) => {
      req.on("response", (res) => {
        res.pause();
        resolve();
      });
    });
    req.on("error", () => {});
    req.end();
    await paused;
    const big = "z".repeat(16 * 1024);
    const started = Date.now();
    for (let i = 0; i < 150; i++) {
      const res = await call(url, "POST", `/v1/conversations/${id}/submissions`, {
        headers: AUTH,
        body: { text: `${big}${i}`, type: "write" },
      });
      expect(res.status).toBe(202);
    }
    expect(Date.now() - started).toBeLessThan(15_000);
    req.destroy();
  });
});
