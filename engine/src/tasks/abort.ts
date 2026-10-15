/**
 * Abort (R7).
 *
 * Aborting a task first records the request durably (`$abort` in `signals`)
 * on the task and all of its live owned work, in one commit. From then on:
 * - running handlers in that subtree have their context cancelled, and any
 *   commit they still attempt is rejected;
 * - nothing in the subtree is scheduled, woken or completed normally;
 * - each task finishes bottom-up: once its owned work is terminal and its
 *   handler (if any) has exited, its `onAbort` runs and it becomes terminal
 *   `aborted` with the requested reason.
 *
 * The request survives a restart, so an interrupted abort resumes (and
 * `onAbort` may run again). Work created later under an aborting owner
 * inherits the abort.
 */
import type { JsonValue } from "../core/json.js";
import type { TaskRecord } from "../storage/types.js";
import { ABORT_KEY, type TaskLookup } from "./define.js";
import { ancestors, hasLiveOwned, subtreeBottomUp } from "./ownership.js";

/** Reason of a pending abort request on the task, or null. */
export function abortReason(task: TaskRecord): string | null {
  const req = task.signals[ABORT_KEY] as { reason?: unknown } | undefined;
  if (!req) return null;
  return typeof req.reason === "string" ? req.reason : "aborted";
}

/** The task with an abort request recorded (keeps an earlier request). */
export function withAbortRequest(task: TaskRecord, reason: string): TaskRecord {
  if (abortReason(task) !== null) return task;
  return { ...task, signals: { ...task.signals, [ABORT_KEY]: { reason } as JsonValue } };
}

/** Live tasks of `id`'s subtree (including `id`) not yet carrying an abort request. */
export function abortTargets(lookup: TaskLookup, id: string): TaskRecord[] {
  return subtreeBottomUp(lookup, id).flatMap((node) => {
    const t = lookup.get(node);
    return t && t.status !== "terminal" && abortReason(t) === null ? [t] : [];
  });
}

/** The abort reason a live task inherits from its nearest aborting owner, or null. */
export function inheritedAbort(lookup: TaskLookup, task: TaskRecord): string | null {
  for (const id of ancestors(lookup, task.id)) {
    const owner = lookup.get(id);
    const reason = owner ? abortReason(owner) : null;
    if (reason !== null) return reason;
  }
  return null;
}

/** Whether an aborting task's owned work is all terminal, so it can finish. */
export function abortReady(lookup: TaskLookup, task: TaskRecord): boolean {
  return task.status !== "terminal" && abortReason(task) !== null && !hasLiveOwned(lookup, task.id);
}

/** The task terminal with outcome `aborted`. */
export function aborted(task: TaskRecord, reason: string): TaskRecord {
  return { ...task, status: "terminal", waiting: null, outcome: { type: "aborted", reason } };
}
