import { afterEach, describe, expect, it } from "vitest";
import { roleAllowsLane } from "../../src/server/role.js";
import { defineTask, done } from "../../src/tasks/define.js";
import { memoryBackend } from "../agent/support.js";
import { AUTH, call, serve } from "./support.js";

const noop = () =>
  defineTask<null, null>({
    kind: "test.noop",
    phases: { run: async (_t, rt) => rt.commit(() => done({ ok: true })) },
  });

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  for (const fn of cleanups.splice(0).reverse()) await fn();
});

async function start(role?: "all" | "interactive" | "background") {
  const fx = await memoryBackend.fixture();
  const w = await fx.open({ settings: { progressIntervalMs: 0 }, tasks: [noop()] });
  const srv = await serve(w.harness, role === undefined ? {} : { role });
  cleanups.push(() => w.crash());
  cleanups.push(() => srv.close());
  return srv.url;
}

describe("roleAllowsLane", () => {
  it("lets `all` take both lanes and a role only its own", () => {
    expect(roleAllowsLane("all", "interactive")).toBe(true);
    expect(roleAllowsLane("all", "background")).toBe(true);
    expect(roleAllowsLane("interactive", "interactive")).toBe(true);
    expect(roleAllowsLane("interactive", "background")).toBe(false);
    expect(roleAllowsLane("background", "background")).toBe(true);
    expect(roleAllowsLane("background", "interactive")).toBe(false);
  });
});

describe("session role guard", () => {
  it("reports the role on health, `all` by default", async () => {
    expect((await call(await start(), "GET", "/v1/health")).body.role).toBe("all");
    expect((await call(await start("background"), "GET", "/v1/health")).body.role).toBe(
      "background",
    );
  });

  it("an unrestricted session takes both lanes (the single-worker behaviour)", async () => {
    const url = await start();
    for (const agent of [{}, { lane: "interactive" }, { lane: "background" }]) {
      const res = await call(url, "POST", "/v1/conversations", { headers: AUTH, body: { agent } });
      expect(res.status).toBe(201);
    }
  });

  it("an interactive session refuses background-lane conversations, runs and reconfiguration", async () => {
    const url = await start("interactive");
    const refused = await call(url, "POST", "/v1/conversations", {
      headers: AUTH,
      body: { agent: { lane: "background" } },
    });
    expect(refused.status).toBe(409);
    expect(refused.body).toMatchObject({ error: "wrong_session_role", role: "interactive" });
    const run = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { id: "r1", kind: "test.noop", agent: { lane: "background" } },
    });
    expect(run.status).toBe(409);
    expect(run.body.error).toBe("wrong_session_role");

    const ok = await call(url, "POST", "/v1/conversations", { headers: AUTH, body: { agent: {} } });
    expect(ok.status).toBe(201);
    const id = ok.body.conversationId as string;
    const put = await call(url, "PUT", `/v1/conversations/${id}/agent`, {
      headers: AUTH,
      body: { lane: "background" },
    });
    expect(put.status).toBe(409);
    // A change that does not name a lane is not a lane change.
    const fine = await call(url, "PUT", `/v1/conversations/${id}/agent`, {
      headers: AUTH,
      body: { systemPrompt: "hello" },
    });
    expect(fine.status).toBe(200);
  });

  it("a background session refuses interactive conversations and takes background ones", async () => {
    const url = await start("background");
    const refused = await call(url, "POST", "/v1/conversations", { headers: AUTH, body: {} });
    expect(refused.status).toBe(409);
    expect(refused.body).toMatchObject({ error: "wrong_session_role", role: "background" });
    const run = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { id: "r2", kind: "test.noop", agent: { lane: "background" } },
    });
    expect(run.status).toBe(201);
    const conv = await call(url, "POST", "/v1/conversations", {
      headers: AUTH,
      body: { agent: { lane: "background" } },
    });
    expect(conv.status).toBe(201);
  });
});
