/**
 * The task runtime suite, run against every storage backend.
 */
import { describe, expect, it } from "vitest";
import type { JsonValue } from "../../src/core/json.js";
import type { CommitEvent } from "../../src/session/publish.js";
import type { TaskRecord } from "../../src/storage/types.js";
import {
  defineTask,
  done,
  next,
  SignalRejected,
  WaitRejected,
  waiting,
} from "../../src/tasks/define.js";
import type { AgentBackend } from "../agent/support.js";
import { type Backend, deferred, type Fixture, untilAborted, type Worker } from "./support.js";

const HOUR = 60 * 60 * 1000;

async function waitFor(predicate: () => boolean, timeoutMs = 5_000): Promise<void> {
  const start = Date.now();
  while (!predicate()) {
    if (Date.now() - start > timeoutMs) throw new Error("condition not reached");
    await new Promise((r) => setTimeout(r, 5));
  }
}

async function runningTasks(w: Worker): Promise<TaskRecord[]> {
  return (await w.session.reader.listTasks({ statuses: ["running"] })).items;
}

/** A child that waits for signal "go", then finishes, or throws when its input says so. */
function childDef(log: string[] = []) {
  return defineTask<{ name: string; fail?: boolean }, null>({
    kind: "child",
    phases: {
      start: async (_task, rt) => rt.commit(() => waiting({ signal: "go", phase: "finish" })),
      finish: async (task, rt) => {
        if (task.input.fail) throw new Error(`${task.input.name} failed`);
        return rt.commit(() => done({ name: task.input.name }));
      },
    },
    onAbort: async (task) => {
      log.push(`abort:${task.input.name}`);
    },
  });
}

/** A parent that spawns three children in the same commit that starts its wait. */
function parentDef(policy: "allSettled" | "failFast", log: string[] = []) {
  return defineTask<{ names: string[]; fail?: string }, { children: string[] }>({
    kind: "parent",
    phases: {
      spawn: async (task, rt) =>
        rt.commit((_tx, ops) => {
          const children = task.input.names.map(
            (name) =>
              ops.spawn(childDef(log), {
                input: name === task.input.fail ? { name, fail: true } : { name },
              }).id,
          );
          return waiting({ on: children, policy, phase: "collect", checkpoint: { children } });
        }),
      collect: async (task, rt) =>
        rt.commit(() =>
          done({
            wake: task.wake?.reason ?? null,
            outcomes: task.checkpoint.children.map(
              (id) => rt.task(id)?.outcome as unknown as JsonValue,
            ),
          }),
        ),
    },
    onAbort: async (task) => {
      log.push(`abort:parent:${task.id}`);
    },
  });
}

export function defineTaskRuntimeSuite(backend: Backend): void {
  describe(`task runtime (${backend.name})`, () => {
    let fx: Fixture;
    const setup = async () => {
      fx = await backend.fixture();
      return fx;
    };

    it("resumes a three-phase task at phase 2 with phase 1's checkpoint after a reopen", async () => {
      await setup();
      const calls: string[] = [];
      const reachedTwo = deferred();
      const three = (blockInTwo: boolean) =>
        defineTask<{ n: number }, { trail: string[] }>({
          kind: "three",
          phases: {
            one: async (_task, rt) => {
              calls.push("one");
              return rt.commit(() => next("two", { trail: ["one"] }));
            },
            two: async (task, rt, ctx) => {
              calls.push(`two:${task.checkpoint.trail.join(",")}`);
              if (blockInTwo) {
                reachedTwo.resolve();
                await untilAborted(ctx.signal);
              }
              return rt.commit(() => next("three", { trail: [...task.checkpoint.trail, "two"] }));
            },
            three: async (task, rt) =>
              rt.commit(() =>
                done({ trail: [...task.checkpoint.trail, "three"], n: task.input.n }),
              ),
          },
        });

      const w1 = await fx.open([three(true)]);
      const task = await w1.runtime.spawn(three(true), {
        conversationId: fx.conversationId,
        input: { n: 7 },
      });
      await reachedTwo.promise;
      await w1.crash();

      const w2 = await fx.open([three(false)]);
      const stored = await w2.session.reader.getTask(task.id);
      expect(stored).toMatchObject({ phase: "two", checkpoint: { trail: ["one"] } });
      const final = await w2.runtime.waitForTask(task.id);
      expect(final.outcome).toEqual({
        type: "done",
        value: { trail: ["one", "two", "three"], n: 7 },
      });
      expect(calls).toEqual(["one", "two:one", "two:one"]);
      await w2.crash();
    });

    it("allSettled resumes only after all three children are terminal", async () => {
      await setup();
      const parent = parentDef("allSettled");
      const w = await fx.open([parent, childDef()]);
      const p = await w.runtime.spawn(parent, {
        conversationId: fx.conversationId,
        input: { names: ["a", "b", "c"], fail: "b" },
      });
      await w.runtime.waitForIdle();
      const children = (w.runtime.task(p.id)?.waiting?.on ?? []) as string[];
      expect(children).toHaveLength(3);
      expect(w.runtime.task(p.id)?.status).toBe("waiting");

      await w.runtime.signal(children[0] as string, "go", null);
      await w.runtime.signal(children[1] as string, "go", null);
      await w.runtime.waitForIdle();
      expect(w.runtime.task(p.id)?.status).toBe("waiting");

      await w.runtime.signal(children[2] as string, "go", null);
      const final = await w.runtime.waitForTask(p.id);
      expect(final.outcome).toEqual({
        type: "done",
        value: {
          wake: "settled",
          outcomes: [
            { type: "done", value: { name: "a" } },
            { type: "faulted", message: "b failed", detail: { name: "Error" } },
            { type: "done", value: { name: "c" } },
          ],
        },
      });
      await w.crash();
    });

    it("failFast aborts the other two children on the first failure", async () => {
      await setup();
      const log: string[] = [];
      const parent = parentDef("failFast", log);
      const w = await fx.open([parent, childDef(log)]);
      const p = await w.runtime.spawn(parent, {
        conversationId: fx.conversationId,
        input: { names: ["a", "b", "c"], fail: "a" },
      });
      await w.runtime.waitForIdle();
      const children = (w.runtime.task(p.id)?.waiting?.on ?? []) as string[];
      await w.runtime.signal(children[0] as string, "go", null);
      const final = await w.runtime.waitForTask(p.id);
      const value = (final.outcome as unknown as { value: { outcomes: TaskRecord["outcome"][] } })
        .value;
      expect(value.outcomes[0]).toMatchObject({ type: "faulted", message: "a failed" });
      expect(value.outcomes[1]).toEqual({ type: "aborted", reason: "sibling_failed" });
      expect(value.outcomes[2]).toEqual({ type: "aborted", reason: "sibling_failed" });
      expect(log.sort()).toEqual(["abort:b", "abort:c"]);
      await w.crash();
    });

    it("aborting a parent runs the children's onAbort first and leaves nothing running", async () => {
      await setup();
      const log: string[] = [];
      const cancelled = deferred<unknown>();
      const busyStarted = deferred();
      const busy = defineTask<null, null>({
        kind: "busy",
        phases: {
          start: async (_task, _rt, ctx) => {
            busyStarted.resolve();
            try {
              return await untilAborted(ctx.signal);
            } catch (err) {
              cancelled.resolve(err);
              throw err;
            }
          },
        },
        onAbort: async () => {
          log.push("abort:busy");
        },
      });
      const parent = defineTask<null, null>({
        kind: "tree",
        phases: {
          start: async (_task, rt) =>
            rt.commit((_tx, ops) => {
              const a = ops.spawn(childDef(log), { input: { name: "a" } });
              const b = ops.spawn(busy, { input: null });
              return waiting({ on: [a.id, b.id] });
            }),
        },
        onAbort: async () => {
          log.push("abort:parent");
        },
      });
      const w = await fx.open([parent, childDef(log), busy], { concurrency: 4 });
      const p = await w.runtime.spawn(parent, { conversationId: fx.conversationId, input: null });
      await busyStarted.promise;
      await waitFor(() => w.runtime.tasks({ status: "waiting" }).length === 2);
      // "busy" is mid-handler and holds a running slot.
      expect((await runningTasks(w)).map((t) => t.kind)).toEqual(["busy"]);

      expect(await w.runtime.abort(p.id, "user")).toBe(true);
      const final = await w.runtime.waitForTask(p.id);
      expect(final.outcome).toEqual({ type: "aborted", reason: "user" });
      expect(await cancelled.promise).toBeDefined();
      expect(log.indexOf("abort:parent")).toBe(2);
      expect(log.slice(0, 2).sort()).toEqual(["abort:a", "abort:busy"]);
      await w.runtime.waitForIdle();
      expect(await runningTasks(w)).toEqual([]);
      const all = (await w.session.reader.listTasks()).items;
      expect(all.every((t) => t.outcome?.type === "aborted")).toBe(true);
      await w.crash();
    });

    it("a signal wait survives a reopen, wakes on the signal, and rejects a second signal", async () => {
      await setup();
      const approval = defineTask<null, null>({
        kind: "approval",
        phases: {
          ask: async (_task, rt) =>
            rt.commit(() => waiting({ signal: "approval", phase: "decided" })),
          decided: async (task, rt) =>
            rt.commit(() =>
              done({ wake: task.wake?.reason ?? null, decision: task.signals.approval ?? null }),
            ),
        },
      });
      const w1 = await fx.open([approval]);
      const t = await w1.runtime.spawn(approval, {
        conversationId: fx.conversationId,
        input: null,
      });
      await w1.runtime.waitForIdle();
      expect(w1.runtime.task(t.id)?.status).toBe("waiting");
      expect(await runningTasks(w1)).toEqual([]);
      await w1.crash();

      const w2 = await fx.open([approval]);
      await w2.runtime.waitForIdle();
      expect(w2.runtime.task(t.id)?.status).toBe("waiting");
      await w2.runtime.signal(t.id, "approval", { approve: true });
      const final = await w2.runtime.waitForTask(t.id);
      expect(final.outcome).toEqual({
        type: "done",
        value: { wake: "signal", decision: { approve: true } },
      });
      await expect(w2.runtime.signal(t.id, "approval", { approve: false })).rejects.toBeInstanceOf(
        SignalRejected,
      );
      await w2.crash();
    });

    it("a second signal for a consumed key is rejected while the task is still live", async () => {
      await setup();
      const slow = defineTask<null, null>({
        kind: "slow",
        phases: {
          wait: async (_task, rt) => rt.commit(() => waiting({ signal: "k", phase: "hold" })),
          hold: async (_task, rt) => rt.commit(() => waiting({ signal: "never" })),
        },
      });
      const w = await fx.open([slow]);
      const t = await w.runtime.spawn(slow, { conversationId: fx.conversationId, input: null });
      await w.runtime.waitForIdle();
      await w.runtime.signal(t.id, "k", 1);
      await w.runtime.waitForIdle();
      expect(w.runtime.task(t.id)?.waiting?.signal).toBe("never");
      await expect(w.runtime.signal(t.id, "k", 2)).rejects.toBeInstanceOf(SignalRejected);
      await expect(w.runtime.signal("task_missing", "k", 2)).rejects.toBeInstanceOf(SignalRejected);
      await w.crash();
    });

    it("a two-hour until wait wakes once the clock passes two hours, across a reopen", async () => {
      await setup();
      const sleeper = defineTask<null, null>({
        kind: "sleeper",
        phases: {
          sleep: async (_task, rt) =>
            rt.commit(() => waiting({ until: fx.clock.now() + 2 * HOUR, phase: "woke" })),
          woke: async (task, rt) => rt.commit(() => done({ wake: task.wake?.reason ?? null })),
        },
      });
      const w1 = await fx.open([sleeper]);
      const t = await w1.runtime.spawn(sleeper, { conversationId: fx.conversationId, input: null });
      await w1.runtime.waitForIdle();
      fx.clock.advance(HOUR);
      await w1.runtime.waitForIdle();
      expect(w1.runtime.task(t.id)?.status).toBe("waiting");
      await w1.crash();

      const w2 = await fx.open([sleeper]);
      await w2.runtime.waitForIdle();
      fx.clock.advance(HOUR - 1);
      await w2.runtime.waitForIdle();
      expect(w2.runtime.task(t.id)?.status).toBe("waiting");
      fx.clock.advance(1);
      const final = await w2.runtime.waitForTask(t.id);
      expect(final.outcome).toEqual({ type: "done", value: { wake: "deadline" } });
      await w2.crash();
    });

    it("rejects a wait on yourself or on your owner chain", async () => {
      await setup();
      const results: string[] = [];
      const grandchild = defineTask<{ root: string; parent: string }, null>({
        kind: "grandchild",
        phases: {
          start: async (task, rt) => {
            const targets = { self: task.id, parent: task.input.parent, root: task.input.root };
            for (const [label, id] of Object.entries(targets)) {
              try {
                await rt.commit(() => waiting({ on: [id] }));
                results.push(`${label}:accepted`);
              } catch (err) {
                results.push(`${label}:${err instanceof WaitRejected ? "rejected" : String(err)}`);
              }
            }
            return rt.commit(() => done(null));
          },
        },
      });
      const middle = defineTask<{ root: string }, null>({
        kind: "middle",
        phases: {
          start: async (task, rt) =>
            rt.commit((_tx, ops) => {
              const g = ops.spawn(grandchild, {
                input: { root: task.input.root, parent: task.id },
              });
              return waiting({ on: [g.id], phase: "end" });
            }),
          end: async (_task, rt) => rt.commit(() => done(null)),
        },
      });
      const root = defineTask<null, null>({
        kind: "root",
        phases: {
          start: async (task, rt) =>
            rt.commit((_tx, ops) => {
              const m = ops.spawn(middle, { input: { root: task.id } });
              return waiting({ on: [m.id], phase: "end" });
            }),
          end: async (_task, rt) => rt.commit(() => done(null)),
        },
      });
      const w = await fx.open([root, middle, grandchild]);
      const r = await w.runtime.spawn(root, { conversationId: fx.conversationId, input: null });
      const final = await w.runtime.waitForTask(r.id);
      expect(results).toEqual(["self:rejected", "parent:rejected", "root:rejected"]);
      expect(final.outcome).toEqual({ type: "done", value: null });
      await w.crash();
    });

    it("rejects a wait that would close a cycle through another task's wait", async () => {
      await setup();
      const outcome = deferred<string>();
      const gate = deferred();
      const y = defineTask<null, null>({
        kind: "cycle-y",
        phases: {
          start: async (_task, rt) => {
            await gate.promise;
            const x = rt.runtime.tasks().find((t) => t.kind === "cycle-x");
            try {
              await rt.commit(() => waiting({ on: [x?.id as string] }));
              outcome.resolve("accepted");
            } catch (err) {
              outcome.resolve(err instanceof WaitRejected ? "rejected" : String(err));
            }
            return rt.commit(() => done(null));
          },
        },
      });
      const x = defineTask<{ y: string }, null>({
        kind: "cycle-x",
        phases: {
          start: async (task, rt) => rt.commit(() => waiting({ on: [task.input.y], phase: "end" })),
          end: async (_task, rt) => rt.commit(() => done("x")),
        },
      });
      const w = await fx.open([x, y]);
      const ty = await w.runtime.spawn(y, { conversationId: fx.conversationId, input: null });
      const tx = await w.runtime.spawn(x, {
        conversationId: fx.conversationId,
        input: { y: ty.id },
      });
      await waitFor(() => w.runtime.task(tx.id)?.status === "waiting");
      gate.resolve();
      expect(await outcome.promise).toBe("rejected");
      expect((await w.runtime.waitForTask(tx.id)).outcome).toEqual({ type: "done", value: "x" });
      await w.crash();
    });

    it("a throwing handler ends faulted and does not fail the session", async () => {
      await setup();
      const boom = defineTask<null, null>({
        kind: "boom",
        phases: {
          start: async () => {
            throw new TypeError("kaboom");
          },
        },
      });
      const fine = defineTask<null, null>({
        kind: "fine",
        phases: { start: async (_task, rt) => rt.commit(() => done("ok")) },
      });
      const w = await fx.open([boom, fine]);
      const b = await w.runtime.spawn(boom, { conversationId: fx.conversationId, input: null });
      const final = await w.runtime.waitForTask(b.id);
      expect(final.outcome).toEqual({
        type: "faulted",
        message: "kaboom",
        detail: { name: "TypeError" },
      });
      expect(w.session.state).toBe("open");
      const f = await w.runtime.spawn(fine, { conversationId: fx.conversationId, input: null });
      expect((await w.runtime.waitForTask(f.id)).outcome).toEqual({ type: "done", value: "ok" });
      await w.crash();
    });

    it("faults a handler that does not return a committed transition, or names an unknown phase", async () => {
      await setup();
      const lazy = defineTask<null, null>({
        kind: "lazy",
        phases: { start: async () => ({}) as never },
      });
      const lost = defineTask<null, null>({
        kind: "lost",
        phases: { start: async (_task, rt) => rt.commit(() => next("nowhere", null)) },
      });
      const w = await fx.open([lazy, lost]);
      const a = await w.runtime.spawn(lazy, { conversationId: fx.conversationId, input: null });
      const b = await w.runtime.spawn(lost, { conversationId: fx.conversationId, input: null });
      expect((await w.runtime.waitForTask(a.id)).outcome?.type).toBe("faulted");
      expect((await w.runtime.waitForTask(b.id)).outcome).toMatchObject({
        type: "faulted",
        message: expect.stringContaining("nowhere"),
      });
      await w.crash();
    });

    it("aborts an abandonOnRestart task with reason restart at the next start", async () => {
      await setup();
      const log: string[] = [];
      const ephemeral = defineTask<null, null>({
        kind: "ephemeral",
        phases: { start: async (_task, rt) => rt.commit(() => waiting({ signal: "never" })) },
        onAbort: async () => {
          log.push("onAbort");
        },
      });
      const w1 = await fx.open([ephemeral]);
      const t = await w1.runtime.spawn(ephemeral, {
        conversationId: fx.conversationId,
        input: null,
        abandonOnRestart: true,
      });
      await w1.runtime.waitForIdle();
      await w1.crash();

      const w2 = await fx.open([ephemeral]);
      const final = await w2.runtime.waitForTask(t.id);
      expect(final.outcome).toEqual({ type: "aborted", reason: "restart" });
      expect(log).toEqual(["onAbort"]);
      await w2.crash();
    });

    it("keeps a task completing while its owned work is live", async () => {
      await setup();
      const quick = defineTask<null, null>({
        kind: "quick",
        phases: {
          start: async (_task, rt) =>
            rt.commit((_tx, ops) => {
              ops.spawn(childDef(), { input: { name: "kid" } });
              return done("parent-result");
            }),
        },
      });
      const w = await fx.open([quick, childDef()]);
      const q = await w.runtime.spawn(quick, { conversationId: fx.conversationId, input: null });
      await w.runtime.waitForIdle();
      expect(w.runtime.task(q.id)).toMatchObject({ status: "completing", outcome: null });
      const kid = w.runtime.tasks().find((t) => t.kind === "child");
      await w.runtime.signal(kid?.id as string, "go", null);
      const final = await w.runtime.waitForTask(q.id);
      expect(final.outcome).toEqual({ type: "done", value: "parent-result" });
      await w.crash();
    });

    it("commits a transition atomically with the handler's other writes", async () => {
      await setup();
      const writer = defineTask<null, null>({
        kind: "writer",
        phases: {
          start: async (task, rt) =>
            rt.commit((tx) => {
              tx.appendEntry({
                conversationId: task.conversationId,
                taskId: task.id,
                kind: "note",
                payload: "hello",
              });
              return done(null);
            }),
        },
      });
      const w = await fx.open([writer]);
      const events: CommitEvent[] = [];
      w.session.subscribeCommits((e) => events.push(e));
      const t = await w.runtime.spawn(writer, { conversationId: fx.conversationId, input: null });
      await w.runtime.waitForTask(t.id);
      const final = events.find((e) => e.batch.appendEntries?.length);
      expect(final?.batch.replaceTasks?.[0]).toMatchObject({ id: t.id, status: "terminal" });
      await w.crash();
    });

    it("runs at most `concurrency` handlers at once", async () => {
      await setup();
      let live = 0;
      let peak = 0;
      const release = deferred();
      const work = defineTask<null, null>({
        kind: "work",
        phases: {
          start: async (_task, rt) => {
            live++;
            peak = Math.max(peak, live);
            await release.promise;
            live--;
            return rt.commit(() => done(null));
          },
        },
      });
      const w = await fx.open([work], { concurrency: 2 });
      const ids: string[] = [];
      for (let i = 0; i < 5; i++) {
        ids.push(
          (await w.runtime.spawn(work, { conversationId: fx.conversationId, input: null })).id,
        );
      }
      await new Promise((r) => setTimeout(r, 50));
      expect(live).toBe(2);
      release.resolve();
      for (const id of ids) await w.runtime.waitForTask(id);
      expect(peak).toBe(2);
      await w.crash();
    });

    it("cancels running handlers when the lease signal aborts, and commits nothing more", async () => {
      await setup();
      const stopped = deferred<unknown>();
      const started = deferred();
      const holder = defineTask<null, null>({
        kind: "holder",
        phases: {
          start: async (_task, rt, ctx) => {
            started.resolve();
            try {
              await untilAborted(ctx.signal);
            } catch (err) {
              stopped.resolve(err);
            }
            return rt.commit(() => done("too late"));
          },
        },
      });
      const w = await fx.open([holder]);
      const t = await w.runtime.spawn(holder, { conversationId: fx.conversationId, input: null });
      await started.promise;
      await w.loseLease();
      expect(await stopped.promise).toBeDefined();
      await w.runtime.closed;
      expect(w.runtime.state).toBe("stopped");
      expect((await w.session.reader.getTask(t.id))?.status).toBe("running");
      await w.runtime.close();
      await w.session.close();
    });

    it("aborts work in a conversation owned by a task before the task itself", async () => {
      await setup();
      const log: string[] = [];
      const sub = defineTask<null, { child: string }>({
        kind: "subagent",
        phases: {
          start: async (task, rt) =>
            rt.commit((tx, ops) => {
              const conv = tx.createConversation({
                tenantId: "t1",
                projectId: "p1",
                ownerTaskId: task.id,
              });
              const answer = ops.spawn(childDef(log), {
                conversationId: conv.id,
                parentTaskId: null,
                input: { name: "answer" },
              });
              return waiting({ on: [answer.id], phase: "read", checkpoint: { child: answer.id } });
            }),
          read: async (task, rt) =>
            rt.commit(() => done(rt.task(task.checkpoint.child)?.outcome as unknown as JsonValue)),
        },
        onAbort: async () => {
          log.push("abort:subagent");
        },
      });
      const w = await fx.open([sub, childDef(log)]);
      const s = await w.runtime.spawn(sub, { conversationId: fx.conversationId, input: null });
      await w.runtime.waitForIdle();
      await w.runtime.abortConversation(fx.conversationId, "stop");
      expect((await w.runtime.waitForTask(s.id)).outcome).toEqual({
        type: "aborted",
        reason: "stop",
      });
      expect(log).toEqual(["abort:answer", "abort:subagent"]);

      // And the happy path: the owner wakes when the child conversation's task settles.
      const s2 = await w.runtime.spawn(sub, { conversationId: fx.conversationId, input: null });
      await w.runtime.waitForIdle();
      const child = (w.runtime.task(s2.id)?.waiting?.on ?? [])[0] as string;
      await w.runtime.signal(child, "go", null);
      expect((await w.runtime.waitForTask(s2.id)).outcome).toEqual({
        type: "done",
        value: { type: "done", value: { name: "answer" } },
      });
      await w.crash();
    });

    it("keeps an early signal and wakes at once when the task later waits on it", async () => {
      await setup();
      const gate = deferred();
      const late = defineTask<null, null>({
        kind: "late",
        phases: {
          start: async (_task, rt) => {
            await gate.promise;
            return rt.commit(() => waiting({ signal: "s", phase: "end" }));
          },
          end: async (task, rt) => rt.commit(() => done(task.signals.s ?? null)),
        },
      });
      const w = await fx.open([late]);
      const t = await w.runtime.spawn(late, { conversationId: fx.conversationId, input: null });
      await w.runtime.signal(t.id, "s", "early");
      gate.resolve();
      expect((await w.runtime.waitForTask(t.id)).outcome).toEqual({ type: "done", value: "early" });
      await w.crash();
    });
  });
}

/** A host task: two phases with a deadline wait between them (U3). */
function hostWaitTask() {
  return defineTask<{ until: number }, null>({
    kind: "host.wait",
    phases: {
      start: async (task, rt) =>
        rt.commit(() => waiting({ until: task.input.until, phase: "finish" })),
      finish: async (_task, rt) => rt.commit(() => done({ ok: true })),
    },
  });
}

/**
 * The host-task suite (U3): extra kinds registered through
 * `HarnessOptions.tasks` run like any other task and survive a restart.
 */
export function defineHostTaskSuite(backend: AgentBackend): void {
  describe(`host tasks (${backend.name})`, () => {
    it("runs a host task registered through HarnessOptions.tasks to done", async () => {
      const fx = await backend.fixture();
      const w = await fx.open({ tasks: [hostWaitTask()] });
      const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
      const t = await w.harness.runtime.spawn(hostWaitTask(), {
        conversationId: conv.id,
        input: { until: fx.clock.now() + 10 },
      });
      fx.clock.advance(10);
      const final = await w.harness.runtime.waitForTask(t.id);
      expect(final.outcome).toEqual({ type: "done", value: { ok: true } });
      await w.crash();
    });

    it("resumes a host task waiting({until}) after a restart and wakes at the deadline", async () => {
      const fx = await backend.fixture();
      const w1 = await fx.open({ tasks: [hostWaitTask()] });
      const conv = await w1.harness.createConversation({ tenantId: "t1", projectId: "p1" });
      const t = await w1.harness.runtime.spawn(hostWaitTask(), {
        conversationId: conv.id,
        input: { until: fx.clock.now() + 200 },
      });
      await w1.harness.runtime.waitForIdle();
      expect(w1.harness.runtime.task(t.id)?.status).toBe("waiting");
      await w1.crash();

      const w2 = await fx.open({ tasks: [hostWaitTask()] });
      await w2.harness.runtime.waitForIdle();
      fx.clock.advance(100);
      await w2.harness.runtime.waitForIdle();
      expect(w2.harness.runtime.task(t.id)?.status).toBe("waiting");
      fx.clock.advance(100);
      const final = await w2.harness.runtime.waitForTask(t.id);
      expect(final.outcome).toEqual({ type: "done", value: { ok: true } });
      await w2.crash();
    });
  });
}
