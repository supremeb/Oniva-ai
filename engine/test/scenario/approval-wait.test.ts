/**
 * AE3: an approval waits more than an hour of scenario time across a worker
 * restart, then an HTTP decision runs the tool once. Also: the shared clock
 * endpoint exists only in the test entry point.
 */
import { describe, expect, it } from "vitest";
import type { ApprovalsState } from "../../src/agent/approvals.js";
import type { ToolResultPayload } from "../../src/agent/context-builder.js";
import {
  AUTH,
  call,
  createConversation,
  PRINCIPAL,
  payloads,
  submit,
  until,
  useScenario,
} from "./support.js";

const sc = useScenario();

const MINUTE = 60_000;

describe("AE3 approval wait", () => {
  it("survives 61 minutes of shared scenario time and a restart, then runs once on approval", async () => {
    await sc.script({
      rules: [
        {
          when: { role: "user", includes: "APPROVE" },
          respond: {
            steps: [{ type: "tool_call", name: "approve_write", arguments: { note: "x" } }],
          },
        },
        {
          when: { role: "tool_result", tool: "approve_write" },
          respond: { steps: [{ type: "text", text: "approved and done" }] },
        },
      ],
    });
    const gw = sc.gateway.gw;
    const before = gw.effects("approve_write");
    const env = { ENGINE_APPROVAL_TTL_MS: String(120 * MINUTE) };
    const a = sc.worker("ae3", env);
    const urlA = await a.serving();
    const conversationId = await createConversation(urlA, { tools: ["approve_write"] });
    const sub = await submit(urlA, conversationId, { text: "APPROVE the write", requestId: "r1" });
    const ins = await sc.inspect("ae3");

    const pending = await until(async () => {
      const doc = await ins.doc<ApprovalsState>("agent.approvals", conversationId);
      return doc?.items.find((i) => i.status === "pending");
    }, "a pending approval");
    expect(gw.effects("approve_write") - before).toBe(0);

    const advanced = await call(urlA, "POST", "/v1/test/clock", {
      headers: AUTH,
      body: { advanceMs: 61 * MINUTE },
    });
    expect(advanced.status).toBe(200);
    expect(advanced.body.offsetMs).toBeGreaterThanOrEqual(61 * MINUTE);

    a.kill("SIGKILL");
    await a.exited;
    const b = sc.worker("ae3", env);
    const urlB = await b.serving();
    const clockB = await call(urlB, "GET", "/v1/test/clock", { headers: AUTH });
    expect(clockB.body.offsetMs).toBeGreaterThanOrEqual(61 * MINUTE);
    expect(clockB.body.now - pending.requestedAt).toBeGreaterThanOrEqual(61 * MINUTE);

    // Still pending: nothing ran while it waited.
    const still = await ins.doc<ApprovalsState>("agent.approvals", conversationId);
    expect(still?.items.find((i) => i.id === pending.id)?.status).toBe("pending");
    expect(gw.effects("approve_write") - before).toBe(0);

    const decided = await call(
      urlB,
      "POST",
      `/v1/conversations/${conversationId}/approvals/${pending.toolTaskId}/decision`,
      { headers: { ...AUTH, ...PRINCIPAL }, body: { decision: "approve", note: "ok" } },
    );
    expect(decided.status).toBe(200);
    expect(decided.body.approval.status).toBe("approved");

    const settled = await ins.settled(sub.body.submissionId);
    expect(settled.status).toBe("done");
    expect((settled.result as { text?: string }).text).toBe("approved and done");
    const results = payloads<ToolResultPayload>(await ins.entries(conversationId), "tool_result");
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({
      status: "ok",
      approval: { status: "approved", principal: "user:alice" },
    });
    expect((results[0]?.approval?.decidedAt ?? 0) - pending.requestedAt).toBeGreaterThanOrEqual(
      61 * MINUTE,
    );
    expect(gw.effects("approve_write") - before).toBe(1);
  });
});

describe("test-only clock endpoint", () => {
  it("is absent from the production worker (404) and guarded in the test harness (401)", async () => {
    const prod = sc.productionWorker("prod-clock");
    const url = await prod.serving();
    const health = await call(url, "GET", "/v1/health");
    expect(health.status).toBe(200);
    expect(health.body.owner).toBe(true);
    expect((await call(url, "GET", "/v1/test/clock", { headers: AUTH })).status).toBe(404);
    const post = await call(url, "POST", "/v1/test/clock", {
      headers: AUTH,
      body: { advanceMs: 1000 },
    });
    expect(post.status).toBe(404);

    const harness = sc.worker("harness-clock");
    const hurl = await harness.serving();
    expect((await call(hurl, "GET", "/v1/test/clock")).status).toBe(401);
    expect((await call(hurl, "GET", "/v1/test/clock", { headers: AUTH })).status).toBe(200);
  });

  it("refuses test-harness configuration under NODE_ENV=production", async () => {
    const prod = sc.productionWorker("prod-refuse", {
      NODE_ENV: "production",
      ENGINE_TEST_HARNESS: "1",
    });
    const exit = await prod.exited;
    expect(exit.code).toBe(2);
    expect(prod.stderr).toMatch(/ENGINE_TEST_HARNESS/);

    const harness = sc.worker("harness-refuse", { NODE_ENV: "production" });
    expect((await harness.exited).code).toBe(2);
    expect(harness.stderr).toMatch(/NODE_ENV=production/);
  });
});
