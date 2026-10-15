/**
 * The Deep Work shape on the engine's fake provider (plan 2026-10-15-002, U11,
 * R19): an orchestrator configured exactly as the backend's
 * `compose_execute_config` does (recorded in `test/fixtures/deep-work/`), with
 * the real gateway client talking to a fake of the `dw_*` tools, runs a round
 * of research workers, a reflector, two sections and `dw_finalize`.
 *
 * The view is asserted at key points, and the golden fixture
 * `test/fixtures/deep-work/deep_work_views.json` records, per scenario, those
 * views and the complete watch frame stream (ids normalized). The frontend
 * adapter tests and the backend contract tests read the same file.
 *
 *     ONIVA_RECORD_CONTRACTS=1 npx vitest run --project unit deep-work-fixtures   # re-record
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, afterEach, describe, expect, it } from "vitest";
import type { Conversation } from "../../src/agent/conversation.js";
import type { Harness } from "../../src/agent/harness.js";
import { createGatewayTools } from "../../src/tools/gateway.js";
import { type WatchFrame, WatchHub } from "../../src/watch/stream.js";
import { type ConversationView, loadConversationView } from "../../src/watch/view.js";
import { memoryBackend, waitFor } from "../agent/support.js";
import {
  deepWorkGatewayTools,
  deepWorkSubagentTools,
  executeAgent,
  FIXTURE_DIR,
  Holds,
  scripted,
  stubWorkerTools,
} from "../support/deep-work.js";
import { FakeGateway } from "../support/fake-gateway.js";
import { ClientView } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const VIEWS_FILE = join(FIXTURE_DIR, "deep_work_views.json");
const RECORD = process.env.ONIVA_RECORD_CONTRACTS === "1";
const PLAN_TEXT =
  'Run this approved research plan.\n\n{"title":"Contract run","sub_queries":[{"worker_id":"w1","sub_query":"q-w1"}]}';

/** What one scenario records. */
interface Recorded {
  /** The legacy fixture this scenario corresponds to (`backend/tests/contracts/fixtures`). */
  legacy: string | null;
  /** Views at the named points of the run, as `loadConversationView` returned them. */
  points: { at: string; view: ConversationView }[];
  /** Every frame the watch stream sent, snapshot first; applying them in order gives the last point's view. */
  frames: WatchFrame[];
}

const recorded: Record<string, Recorded> = {};

const ID = /(?:[a-z]+_)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g;

/** Replace every engine id with a stable ordinal, keeping its prefix (`ent_<id-3>`). */
function normalize<T>(value: T): T {
  const ids = new Map<string, string>();
  const text = JSON.stringify(value).replace(ID, (match) => {
    const prefix = /^[a-z]+_/.exec(match)?.[0] ?? "";
    const raw = match.slice(prefix.length);
    if (!ids.has(raw)) ids.set(raw, `<id-${ids.size + 1}>`);
    return `${prefix}${ids.get(raw)}`;
  });
  return JSON.parse(text) as T;
}

afterAll(() => {
  if (RECORD && Object.keys(recorded).length === 3) {
    writeFileSync(
      VIEWS_FILE,
      `${JSON.stringify(
        Object.fromEntries(Object.entries(recorded).map(([k, v]) => [k, normalize(v)])),
        null,
        2,
      )}\n`,
    );
  }
});

interface Run {
  w: { harness: Harness; crash(): Promise<void> };
  conv: Conversation;
  gw: FakeGateway;
  holds: Holds;
  hub: WatchHub;
  client: ClientView;
  /** Pull the frames the stream has so far into `client`. */
  drain(): Promise<void>;
  view(): Promise<ConversationView>;
  point(at: string): Promise<ConversationView>;
  points: Recorded["points"];
}

const open: (() => Promise<void>)[] = [];
afterEach(async () => {
  for (const close of open.splice(0)) await close();
});

async function start(): Promise<Run> {
  const holds = new Holds();
  const gw = await FakeGateway.start(deepWorkGatewayTools(holds));
  const toolset = await createGatewayTools({
    baseUrl: gw.baseUrl,
    serviceToken: gw.serviceToken,
    fence: () => null,
    retry: { attempts: 1, baseDelayMs: 1, maxDelayMs: 1 },
  });
  const fx = await memoryBackend.fixture();
  // The script is a pure function of the request, so any number of concurrent children can use it.
  fx.provider.enqueue(...Array.from({ length: 300 }, () => scripted));
  const w = await fx.open({
    tools: [...toolset.tools, ...deepWorkSubagentTools(), ...stubWorkerTools(holds)] as Any,
    settings: { progressIntervalMs: 0 },
  });
  const conv = await w.harness.createConversation({
    tenantId: "t1",
    projectId: "p1",
    agent: executeAgent(),
  });
  const hub = new WatchHub(w.harness);
  const consumer = await hub.watch(conv.id);
  const client = new ClientView();
  const points: Recorded["points"] = [];
  const run: Run = {
    w,
    conv,
    gw,
    holds,
    hub,
    client,
    points,
    async drain() {
      for (let f = consumer.poll(); f !== null; f = consumer.poll()) client.apply(f);
    },
    view: () => loadConversationView(w.harness.session, conv.id),
    async point(at) {
      await run.drain();
      const view = await run.view();
      // The stream and a fresh load agree at every point we record.
      expect(client.view).toEqual(view);
      points.push({ at, view: structuredClone(view) });
      return view;
    },
  };
  client.apply((await consumer.next()) as WatchFrame);
  open.push(async () => {
    await hub.close();
    await w.crash();
    await gw.close();
  });
  return run;
}

const subagents = (v: ConversationView) =>
  Object.values(v.subagents).sort((a, b) => a.startedAt.localeCompare(b.startedAt));

async function until<T>(run: Run, pick: (v: ConversationView) => T | false | undefined) {
  let found: T | false | undefined;
  await waitFor(async () => {
    found = pick(await run.view());
    return Boolean(found);
  });
  return found as T;
}

function record(name: string, legacy: string | null, run: Run) {
  recorded[name] = { legacy, points: run.points, frames: run.client.frames };
}

function expected(name: string): Recorded {
  return JSON.parse(readFileSync(VIEWS_FILE, "utf8"))[name];
}

function checkGolden(name: string, run: Run) {
  // The legacy executor has no cancel fixture.
  const legacy = { success: "deep_work_success", failure: "deep_work_failure", cancel: null }[name];
  record(name, legacy ?? null, run);
  if (RECORD) return;
  expect(normalize(recorded[name])).toEqual(expected(name));
}

describe("deep work shape: success", () => {
  it("runs a round, reflects, adds a gap round, writes two sections and finalizes", async () => {
    const run = await start();
    const search = run.holds.add("web_search:q-w1");
    const reflection = run.holds.add("dw_record_reflection");
    const sub = await run.conv.submit({ text: PLAN_TEXT, requestId: "dw-1" });

    // A worker is mid-search: the parent's view shows it without a client-side id.
    await search.entered;
    const running = await until(run, (v) => {
      const [c] = subagents(v);
      return c?.recent.length === 1 ? v : false;
    });
    expect(running.run.active).toBe(true);
    expect(subagents(running)).toHaveLength(1);
    expect(subagents(running)[0]).toMatchObject({
      status: "running",
      toolCalls: 1,
      recent: ['web_search({"query":"q-w1"})'],
    });
    const system = running.entries.find((e) => e.kind === "system") as Any;
    expect(system.payload.tools).toEqual(executeAgent().tools);
    expect(running.agent.metadata).toMatchObject({
      phase: "execute",
      subagents: { research_worker: { model: "m/worker" } },
    });
    expect(running.agent.maxRounds).toBe(40);
    const start1 = running.entries.find(
      (e) => e.kind === "tool_result" && (e.payload as Any).toolName === "dw_start_round",
    ) as Any;
    expect(JSON.parse(start1.payload.content)).toEqual({
      round: 1,
      workers: [{ worker_id: "w1", sub_query: "q-w1" }],
    });
    await run.point("worker-running");

    // Round 1 is saved and the reflector is recording its reflection.
    search.release();
    await reflection.entered;
    const reflecting = await until(run, (v) => {
      const children = subagents(v);
      return children.length === 2 &&
        children[1]?.recent.at(-1)?.startsWith("dw_record_reflection(")
        ? v
        : false;
    });
    const [worker, reflector] = subagents(reflecting);
    expect(worker).toMatchObject({
      status: "done",
      toolCalls: 2,
      recent: [
        'web_search({"query":"q-w1"})',
        expect.stringContaining('dw_save_learnings({"worker_id":"w1"'),
      ],
    });
    expect(typeof worker?.endedAt).toBe("string");
    expect(reflector).toMatchObject({ status: "running", toolCalls: 2 });
    expect(reflector?.recent[0]).toBe("dw_get_learnings()");
    expect(reflecting.run.active).toBe(true);
    await run.point("reflecting");

    reflection.release();
    expect((await sub.wait()).status).toBe("done");
    await run.w.harness.waitForIdle();
    const done = await run.point("done");

    // Four children, in order: w1, reflector, gap-1, reflector.
    const children = subagents(done);
    expect(children.map((c) => c.status)).toEqual(["done", "done", "done", "done"]);
    expect(children.map((c) => c.toolCalls)).toEqual([2, 2, 2, 2]);
    expect(done.run.active).toBe(false);
    expect(done.usage.requests).toBe(10);
    for (const c of children) expect(c.usage.requests).toBeGreaterThan(0);
    const finalText = [...done.entries].reverse().find((e) => e.kind === "assistant") as Any;
    expect(finalText.payload.message.blocks[0].text).toBe("Deep Work complete.");
    const toolNames = done.entries
      .filter((e) => e.kind === "tool_result")
      .map((e) => (e.payload as Any).toolName);
    expect(toolNames).toEqual([
      "dw_start_round",
      "research_worker",
      "reflector",
      "dw_start_round",
      "research_worker",
      "reflector",
      "dw_write_section",
      "dw_write_section",
      "dw_finalize",
    ]);
    const sections = done.entries.filter(
      (e) => e.kind === "tool_result" && (e.payload as Any).toolName === "dw_write_section",
    ) as Any[];
    expect(JSON.parse(sections.at(-1).payload.content).sections).toEqual([
      "Findings",
      "Gaps closed",
    ]);

    // The gateway saw every child's calls under the root conversation id, one effect per call.
    for (const name of ["dw_save_learnings", "dw_record_reflection", "dw_get_learnings"]) {
      for (const r of run.gw.invokes(name)) {
        expect((r.body as Any).conversationId).toBe(run.conv.id);
        expect((r.body as Any).engineConversationId).not.toBe(run.conv.id);
      }
    }
    for (const r of run.gw.invokes("dw_start_round")) {
      expect((r.body as Any).engineConversationId).toBe(run.conv.id);
    }
    expect(run.gw.effects("dw_start_round")).toBe(2);
    expect(run.gw.effects("dw_save_learnings")).toBe(2);
    expect(run.gw.effects("dw_record_reflection")).toBe(2);
    expect(run.gw.effects("dw_write_section")).toBe(2);
    expect(run.gw.effects("dw_finalize")).toBe(1);

    // Replaying the stream rebuilds the final view, and a late watcher gets the same snapshot.
    const late = await run.hub.watch(run.conv.id, { cursor: 1_000_000 });
    const lateView = new ClientView();
    lateView.apply((await late.next()) as WatchFrame);
    expect(lateView.view).toEqual(done);
    checkGolden("success", run);
  });
});

describe("deep work shape: parallel workers", () => {
  it("two workers of one round run at the same time and both show in the parent's view", async () => {
    const run = await start();
    const s1 = run.holds.add("web_search:q-w1");
    const s2 = run.holds.add("web_search:q-w2");
    const sub = await run.conv.submit({ text: "Run the parallel plan", requestId: "dw-p" });
    await Promise.all([s1.entered, s2.entered]);
    const v = await until(run, (x) => {
      const c = subagents(x);
      return c.length === 2 && c.every((k) => k.recent.length === 1) ? x : false;
    });
    expect(subagents(v).map((c) => c.status)).toEqual(["running", "running"]);
    expect(new Set(subagents(v).map((c) => c.conversationId)).size).toBe(2);
    expect(Object.keys(v.subagents)).toHaveLength(2);
    s2.release();
    s1.release();
    expect((await sub.wait()).status).toBe("done");
    await run.w.harness.waitForIdle();
    const done = await run.view();
    // w1, w2, reflector, gap-1, reflector.
    expect(subagents(done)).toHaveLength(5);
    expect(subagents(done).every((c) => c.status === "done")).toBe(true);
    expect(run.gw.effects("dw_save_learnings")).toBe(3);
  });
});

describe("deep work shape: failure", () => {
  it("a worker fault leaves the round going; an orchestrator error ends the run with run_error", async () => {
    const run = await start();
    const reflection = run.holds.add("dw_record_reflection");
    const sub = await run.conv.submit({ text: "Run the failing plan", requestId: "dw-f" });

    // The worker's model failed; the orchestrator got an error result and moved on to reflection.
    await reflection.entered;
    const faulted = await until(run, (v) => {
      const c = subagents(v);
      return c.length === 2 && c[1]?.recent.at(-1)?.startsWith("dw_record_reflection(") ? v : false;
    });
    const [worker, reflector] = subagents(faulted);
    expect(worker?.status).toBe("failed");
    expect(typeof worker?.endedAt).toBe("string");
    expect(worker?.recent).toEqual(['web_search({"query":"q-FAULT"})']);
    expect(reflector?.status).toBe("running");
    const workerResult = faulted.entries.find(
      (e) => e.kind === "tool_result" && (e.payload as Any).toolName === "research_worker",
    ) as Any;
    expect(workerResult.payload.isError).toBe(true);
    expect(workerResult.payload.content).toContain("did not answer");
    expect(faulted.run.active).toBe(true);
    await run.point("worker-faulted");

    reflection.release();
    const settled = await sub.wait();
    expect(settled.status).toBe("unanswered");
    await run.w.harness.waitForIdle();
    const failed = await run.point("run-failed");
    expect(failed.run.active).toBe(false);
    expect(subagents(failed).map((c) => c.status)).toEqual(["failed", "done"]);
    const err = failed.entries.find((e) => e.kind === "run_error") as Any;
    expect(err).toBeDefined();
    expect(err.payload.error).toMatchObject({
      kind: "invalid_request",
      message: expect.stringContaining("orchestrator failed"),
    });
    // The fault is in the worker's row and the failure was reported to the backend.
    const reflectionCall = run.gw.invokes("dw_record_reflection")[0]?.body as Any;
    expect(reflectionCall.args.failed_workers).toEqual(["w1"]);
    expect(run.gw.effects("dw_finalize")).toBe(0);
    checkGolden("failure", run);
  });
});

describe("deep work shape: cancel", () => {
  it("aborting mid-research ends every child before the root and leaves nothing running", async () => {
    const run = await start();
    const search = run.holds.add("web_search:q-w1");
    const sub = await run.conv.submit({ text: PLAN_TEXT, requestId: "dw-c" });
    await search.entered;
    await until(run, (v) => (subagents(v)[0]?.recent.length === 1 ? v : false));
    const running = await run.point("worker-running");
    expect(subagents(running)[0]?.status).toBe("running");

    await run.conv.abort("user pressed stop");
    await sub.wait();
    await run.w.harness.waitForIdle();
    const cancelled = await run.point("cancelled");
    expect(cancelled.run.active).toBe(false);
    expect(subagents(cancelled)).toHaveLength(1);
    expect(subagents(cancelled)[0]?.status).toBe("aborted");
    expect(subagents(cancelled).some((c) => c.status === "running")).toBe(false);
    expect(typeof subagents(cancelled)[0]?.endedAt).toBe("string");
    expect(run.gw.effects("dw_save_learnings")).toBe(0);
    expect(run.gw.effects("dw_finalize")).toBe(0);
    checkGolden("cancel", run);
  });
});
