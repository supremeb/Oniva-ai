/**
 * Scheduling lanes of the agent task kinds. A run's lane is set where the run starts (from its conversation's
 * agent config, see {@link Inbox}) and stored on the run's root generation task as `input.lane`; every later task of
 * the run (next rounds, tool tasks, compaction) finds it there through `input.runId`.
 */

import type { TaskRecord } from "../storage/types.js";
import type { Lane } from "../tasks/define.js";

type RunInput = { runId?: unknown; lane?: unknown } | null;

/** `background` when the task's run was started in the background lane, else `interactive`. */
export function runLane(task: TaskRecord, get: (id: string) => TaskRecord | undefined): Lane {
  const input = task.input as RunInput;
  if (input?.lane === "background") return "background";
  if (typeof input?.runId === "string" && input.runId !== task.id) {
    const root = get(input.runId)?.input as RunInput | undefined;
    if (root?.lane === "background") return "background";
  }
  return "interactive";
}
