/**
 * The memory task kinds a worker registers (plan 2026-10-21-001, U30, KTD18):
 * `memory_curate`. A worker passes them as `hostTasks`, which is what lists
 * them in `/v1/health` `taskKinds`.
 */
import type { TaskDefinition } from "../tasks/define.js";
import { createMemoryCurateTask, type MemoryCurateDeps } from "./curate-task.js";
import type { MemoryGateway } from "./gateway.js";

export function createMemoryTasks(
  gateway: MemoryGateway,
  options: Omit<MemoryCurateDeps, "gateway"> = {},
): readonly TaskDefinition[] {
  return [createMemoryCurateTask({ gateway, ...options })] as unknown as readonly TaskDefinition[];
}
