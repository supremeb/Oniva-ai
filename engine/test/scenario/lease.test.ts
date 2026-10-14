/**
 * AE7: two workers contend for one session. Exactly one serves; the other
 * answers 409 and takes over within TTL plus one heartbeat of the owner dying.
 * A paused owner whose lease was taken over can neither commit nor cause a
 * tool effect once it wakes up, even when the successor makes no gateway
 * call after reconciling the stale owner's write.
 */
import { describe, expect, it } from "vitest";
import type { ToolResultPayload } from "../../src/agent/context-builder.js";
import {
  AUTH,
  call,
  createConversation,
  LEASE_TTL_MS,
  payloads,
  submit,
  until,
  useScenario,
} from "./support.js";

const sc = useScenario();

const HEARTBEAT_MS = LEASE_TTL_MS / 3;

/** Scheduling slack for a process noticing the expiry and logging it. */
const SLACK_MS = 500;

describe("AE7 lease contention", () => {
  it("serves from one worker, answers 409 from the other, and fails over within TTL + heartbeat", async () => {
    const a = sc.worker("ae7-contend");
    const b = sc.worker("ae7-contend");
    const owner = await Promise.race([
      a.waitFor("serving").then(() => a),
      b.waitFor("serving").then(() => b),
    ]);
    const standby = owner === a ? b : a;
    const ownerUrl = await owner.url();
    const standbyUrl = await standby.url();

    expect((await call(ownerUrl, "GET", "/v1/health")).body.owner).toBe(true);
    const health = await call(standbyUrl, "GET", "/v1/health");
    expect(health.status).toBe(200);
    expect(health.body.owner).toBe(false);
    const refused = await call(standbyUrl, "POST", "/v1/conversations", {
      headers: AUTH,
      body: {},
    });
    expect(refused.status).toBe(409);
    expect(refused.body).toEqual({ error: "not_owner", ownerId: owner.ownerId });
    const conversationId = await createConversation(ownerUrl);
    // Still exactly one owner after the standby retried a few times.
    await until(
      () => standby.events.filter((e) => e.event === "standby").length >= 3,
      "standby retries",
    );
    expect(standby.events.some((e) => e.event === "serving")).toBe(false);

    const killedAt = Date.now();
    owner.kill("SIGKILL");
    const acquired = await standby.waitFor("lease_acquired");
    const takeoverMs = acquired.at - killedAt;
    await standby.waitFor("serving");
    const servingMs = (standby.events.find((e) => e.event === "serving")?.at ?? 0) - killedAt;
    console.log(`AE7 takeover: lease after ${takeoverMs} ms, serving after ${servingMs} ms`);
    expect(takeoverMs).toBeLessThanOrEqual(LEASE_TTL_MS + HEARTBEAT_MS + SLACK_MS);
    expect(acquired.token).toBeGreaterThan(
      (owner.events.find((e) => e.event === "lease_acquired")?.token as number) ?? 0,
    );

    // The new owner serves the old owner's conversation.
    const res = await submit(standbyUrl, conversationId, { text: "hi", type: "write" });
    expect(res.status).toBe(202);
  });

  it("a paused owner's post-takeover tool call never executes and it never commits again", async () => {
    await sc.script({
      rules: [
        {
          when: { role: "user", includes: "FENCE" },
          respond: { steps: [{ type: "tool_call", name: "write_once", arguments: { note: "f" } }] },
        },
        {
          when: { role: "tool_result", tool: "write_once" },
          respond: { steps: [{ type: "text", text: "after takeover" }] },
        },
      ],
    });
    const gw = sc.gateway.gw;
    const before = gw.effects("write_once");
    // A's invoke of write_once is delayed "on the wire" long enough to land
    // after the takeover.
    const a = sc.worker("ae7-paused", {
      ENGINE_TEST_INVOKE_PAUSE: JSON.stringify({ write_once: 1000 }),
    });
    const urlA = await a.serving();
    const tokenA = (await a.waitFor("lease_acquired")).token as number;
    const conversationId = await createConversation(urlA, { tools: ["write_once"] });
    const sub = await submit(urlA, conversationId, { text: "FENCE test", requestId: "r1" });
    await a.waitFor("invoke_pending");
    a.kill("SIGSTOP");
    const pausedAt = Date.now();

    const b = sc.worker("ae7-paused");
    const acquired = await b.waitFor("lease_acquired");
    const tokenB = acquired.token as number;
    expect(tokenB).toBeGreaterThan(tokenA);
    expect(acquired.at - pausedAt).toBeLessThanOrEqual(
      LEASE_TTL_MS + HEARTBEAT_MS + SLACK_MS + 2000,
    );

    const ins = await sc.inspect("ae7-paused");
    const settled = await ins.settled(sub.body.submissionId);
    expect(settled.status).toBe("done");
    expect((settled.result as { text?: string }).text).toBe("after takeover");
    const results = payloads<ToolResultPayload>(await ins.entries(conversationId), "tool_result");
    // The successor's reconcile found no outcome (404) and the run ended with
    // no further gateway call. Its fenced manifest load and reconcile alone
    // raised the session's high-water mark.
    expect(results.map((r) => [r.toolName, r.status])).toEqual([["write_once", "interrupted"]]);
    const reconciles = gw.requests.filter(
      (r) =>
        r.path.startsWith("/internal/tools/invocations/") &&
        r.headers["x-engine-session"] === "ae7-paused",
    );
    expect(reconciles.map((r) => r.headers["x-engine-fence"])).toEqual([String(tokenB)]);
    expect(gw.highWater("ae7-paused")).toBe(tokenB);

    // Wake the stale owner: its delayed request reaches the gateway with the
    // old token and is rejected; the worker fences itself and exits.
    a.kill("SIGCONT");
    const exit = await a.exited;
    expect(exit.code).toBe(3);
    await until(
      () => gw.invokes("write_once").some((r) => r.headers["x-engine-fence"] === String(tokenA)),
      "the stale invoke to reach the gateway",
    );
    const stale = gw
      .invokes("write_once")
      .filter((r) => r.headers["x-engine-session"] === "ae7-paused");
    expect(stale.map((r) => r.headers["x-engine-fence"])).toEqual([String(tokenA)]);
    await until(
      () => a.events.find((e) => e.event === "invoke_answered") ?? null,
      "the stale invoke's answer",
    ).then((e) => expect(e.status).toBe(409));
    expect(gw.effects("write_once") - before).toBe(0);
    expect(a.events.some((e) => e.event === "lease_lost")).toBe(true);

    // No commit carries the stale token after the successor's first commit.
    const { rows } = await sc.pg.pool.query<{ seq: string; fence_token: string | null }>(
      "SELECT seq, fence_token FROM engine_commits WHERE session_id = $1 ORDER BY seq",
      ["ae7-paused"],
    );
    const firstB = rows.findIndex((r) => r.fence_token === String(tokenB));
    expect(firstB).toBeGreaterThan(0);
    expect(rows.slice(firstB).every((r) => r.fence_token === String(tokenB))).toBe(true);
    expect(rows.slice(0, firstB).every((r) => r.fence_token === String(tokenA))).toBe(true);
  });
});
