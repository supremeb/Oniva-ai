/**
 * Signals and wake-ups (R7, R10, KTD9, KTD10).
 *
 * A signal is a payload delivered to a task under a key. It is stored in the
 * task's `signals` map, so it survives restarts; a key can be delivered only
 * once. A task waiting on that key wakes in the same commit. A signal that
 * arrives before the task waits is kept, and the later wait wakes at once.
 *
 * A waiting task wakes (back to `pending`, with `$wake` saying why) when its
 * signal has arrived, when every task in `on` is terminal, or when the
 * injected clock reaches `until`. Under `failFast`, the first `on` task that
 * ends other than `done` gets the rest of `on` aborted; the waiter wakes once
 * they have all settled.
 */
import type { JsonValue } from "../core/json.js";
import type { TaskRecord } from "../storage/types.js";
import {
  ABORT_KEY,
  RESERVED_PREFIX,
  SignalRejected,
  type TaskLookup,
  WAKE_KEY,
  type Wake,
  withSignal,
} from "./define.js";

/** The task with `payload` delivered under `key`, woken if it was waiting on it. */
export function deliverSignal(
  task: TaskRecord | undefined,
  taskId: string,
  key: string,
  payload: JsonValue,
  now: number,
): TaskRecord {
  if (typeof key !== "string" || key.length === 0) {
    throw new SignalRejected(taskId, String(key), "key must be a non-empty string");
  }
  if (key.startsWith(RESERVED_PREFIX)) {
    throw new SignalRejected(taskId, key, `keys starting with ${RESERVED_PREFIX} are reserved`);
  }
  if (!task) throw new SignalRejected(taskId, key, "unknown task");
  if (task.status === "terminal") throw new SignalRejected(taskId, key, "task has finished");
  if (ABORT_KEY in task.signals) throw new SignalRejected(taskId, key, "task is being aborted");
  if (Object.hasOwn(task.signals, key)) {
    throw new SignalRejected(taskId, key, "signal already delivered");
  }
  const signalled: TaskRecord = { ...task, signals: { ...task.signals, [key]: payload } };
  if (task.status === "waiting" && task.waiting?.signal === key) {
    return wake(signalled, { reason: "signal", key, at: now });
  }
  return signalled;
}

/** Why a waiting task should wake now, or null. */
export function dueWake(task: TaskRecord, lookup: TaskLookup, now: number): Wake | null {
  const w = task.waiting;
  if (task.status !== "waiting" || w === null) return null;
  if (w.signal !== null && Object.hasOwn(task.signals, w.signal)) {
    return { reason: "signal", key: w.signal, at: now };
  }
  if (w.on.length > 0 && w.on.every((id) => lookup.get(id)?.status === "terminal")) {
    return { reason: "settled", at: now };
  }
  if (w.until !== null && now >= w.until) return { reason: "deadline", at: now };
  return null;
}

/**
 * Under `failFast`, once one awaited task ended other than `done`: the
 * awaited tasks still live (to be aborted). Otherwise empty.
 */
export function failFastVictims(task: TaskRecord, lookup: TaskLookup): string[] {
  const w = task.waiting;
  if (task.status !== "waiting" || w === null || w.policy !== "failFast") return [];
  const records = w.on.map((id) => lookup.get(id));
  const failed = records.some((r) => r?.status === "terminal" && r.outcome?.type !== "done");
  if (!failed) return [];
  return records.flatMap((r) => (r && r.status !== "terminal" ? [r.id] : []));
}

/** The task woken: back to `pending` with the reason recorded. */
export function wake(task: TaskRecord, reason: Wake): TaskRecord {
  return {
    ...task,
    status: "pending",
    waiting: null,
    signals: withSignal(task.signals, WAKE_KEY, reason),
  };
}
