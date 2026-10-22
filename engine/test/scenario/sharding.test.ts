/**
 * Sharding: an interactive session and a background session run side by side as two workers over one
 * database. Each holds its own lease and commit line; a conversation lives in exactly one of them; a crash
 * of one shard does not touch the other; and a stale writer of one shard is still fenced out.
 */
import { describe, expect, it } from "vitest";
import { LeaseLost } from "../../src/core/errors.js";
import { PostgresStorageProvider } from "../../src/storage/postgres/storage.js";
import type { ModelScript } from "../support/scripted-model.js";
import { AUTH, call, createConversation, submit, useScenario } from "./support.js";

const sc = useScenario();

const SCRIPT: ModelScript = {
  rules: [{ when: { role: "user" }, respond: { steps: [{ type: "text", text: "ok" }] } }],
};

const INTERACTIVE = { ENGINE_SESSION_ROLE: "interactive" };
const BACKGROUND = { ENGINE_SESSION_ROLE: "background" };
const BG_AGENT = { lane: "background" };

describe("sharded sessions", () => {
  it("serve at the same time, each reporting its own session and role", async () => {
    const a = sc.worker("shard-both-i", INTERACTIVE);
    const b = sc.worker("shard-both-b", BACKGROUND);
    const urlA = await a.serving();
    const urlB = await b.serving();
    const ha = await call(urlA, "GET", "/v1/health");
    const hb = await call(urlB, "GET", "/v1/health");
    expect(ha.body).toMatchObject({ owner: true, sessionId: "shard-both-i", role: "interactive" });
    expect(hb.body).toMatchObject({ owner: true, sessionId: "shard-both-b", role: "background" });
    // The interactive shard has no workflow task kinds; the background shard has them.
    expect(ha.body.taskKinds).not.toContain("workflow_run");
    expect(hb.body.taskKinds).toContain("workflow_run");
  });

  it("never hold one conversation in two sessions", async () => {
    await sc.script(SCRIPT);
    const a = sc.worker("shard-own-i", INTERACTIVE);
    const b = sc.worker("shard-own-b", BACKGROUND);
    const urlA = await a.serving();
    const urlB = await b.serving();
    const id = await createConversation(urlA);

    // The same id cannot be created on the other shard, even with a lane it accepts.
    const dup = await call(urlB, "POST", "/v1/conversations", {
      headers: AUTH,
      body: { id, agent: BG_AGENT },
    });
    expect(dup.status).toBe(409);
    // ... and the other shard does not know it.
    const missing = await submit(urlB, id, { text: "hi", type: "write" });
    expect(missing.status).toBe(404);
    // A lane the shard's role does not take is refused up front.
    const wrong = await call(urlB, "POST", "/v1/conversations", { headers: AUTH, body: {} });
    expect(wrong.status).toBe(409);
    expect(wrong.body.error).toBe("wrong_session_role");

    const ok = await submit(urlA, id, { text: "hi", type: "write" });
    expect(ok.status).toBe(202);
    const insA = await sc.inspect("shard-own-i");
    const insB = await sc.inspect("shard-own-b");
    expect((await insA.conversations()).map((c) => c.id)).toEqual([id]);
    expect(await insB.conversations()).toEqual([]);
  });

  it("keep serving when the other shard crashes, and each fences its own stale writer", async () => {
    await sc.script(SCRIPT);
    const i1 = sc.worker("shard-crash-i", INTERACTIVE);
    const b = sc.worker("shard-crash-b", BACKGROUND);
    const urlI = await i1.serving();
    const urlB = await b.serving();
    const staleToken = (await i1.waitFor("lease_acquired")).token as number;
    const bToken = (await b.waitFor("lease_acquired")).token as number;
    const convB = await createConversation(urlB, BG_AGENT);
    const convI = await createConversation(urlI);

    i1.kill("SIGKILL");
    await i1.exited;

    // The background shard is unaffected: same lease, still committing.
    expect((await call(urlB, "GET", "/v1/health")).body.owner).toBe(true);
    const sub = await submit(urlB, convB, { text: "still here", type: "write" });
    expect(sub.status).toBe(202);
    const insB = await sc.inspect("shard-crash-b");
    expect((await insB.settled(sub.body.submissionId)).status).toBe("done");
    expect(b.events.filter((e) => e.event === "lease_acquired")).toHaveLength(1);

    // A successor takes the crashed shard over with a higher token and serves its old conversation.
    const i2 = sc.worker("shard-crash-i", INTERACTIVE);
    const urlI2 = await i2.serving();
    const newToken = (await i2.waitFor("lease_acquired")).token as number;
    expect(newToken).toBeGreaterThan(staleToken);
    const resumed = await submit(urlI2, convI, { text: "back", type: "write" });
    expect(resumed.status).toBe(202);

    // The dead owner's token is fenced out of its session...
    const stale = await new PostgresStorageProvider(sc.pg.pool).open("shard-crash-i");
    await expect(
      stale.commit(
        { putDocuments: [{ kind: "scenario.stale", scopeId: "x", data: {}, updatedAt: 1 }] },
        { token: staleToken },
      ),
    ).rejects.toBeInstanceOf(LeaseLost);
    await stale.close();
    // ... and the other shard's lease never changed hands.
    expect((await b.waitFor("lease_acquired")).token).toBe(bToken);
  });
});
