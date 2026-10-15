/**
 * Task ownership (R7).
 *
 * Every task is owned either by another task (`parentTaskId`) or by its
 * conversation. A conversation may itself be owned by a task
 * (`ConversationRecord.ownerTaskId`, e.g. a subagent's child conversation),
 * so a task's owner chain can pass through conversations. Owned work is what
 * a task waits for before it finishes (`completing`) and what is aborted,
 * bottom-up, when it is aborted.
 *
 * {@link TaskIndex} mirrors the session's tasks and conversation owners in
 * memory. It is loaded on the commit line and then updated from every
 * adopted commit, so inside a commit callback it is exactly committed state.
 */
import type { ConversationRecord, TaskRecord } from "../storage/types.js";
import { type TaskLookup, WaitRejected } from "./define.js";

function addTo(map: Map<string, Set<string>>, key: string, value: string): void {
  let set = map.get(key);
  if (!set) {
    set = new Set();
    map.set(key, set);
  }
  set.add(value);
}

/** Committed tasks and conversation ownership of one session. */
export class TaskIndex implements TaskLookup {
  readonly #tasks = new Map<string, TaskRecord>();
  readonly #live = new Set<string>();
  readonly #children = new Map<string, Set<string>>();
  readonly #roots = new Map<string, Set<string>>();
  readonly #convOwner = new Map<string, string | null>();
  readonly #ownedConvs = new Map<string, Set<string>>();

  putConversation(conv: Pick<ConversationRecord, "id" | "ownerTaskId">): void {
    if (this.#convOwner.has(conv.id)) return;
    this.#convOwner.set(conv.id, conv.ownerTaskId);
    if (conv.ownerTaskId !== null) addTo(this.#ownedConvs, conv.ownerTaskId, conv.id);
  }

  /** Create or replace a task. Strips storage's `seq` stamp. */
  putTask(record: TaskRecord): void {
    const { seq: _seq, ...rest } = record as TaskRecord & { seq?: number };
    const rec = rest as TaskRecord;
    if (!this.#tasks.has(rec.id)) {
      if (rec.parentTaskId !== null) addTo(this.#children, rec.parentTaskId, rec.id);
      else addTo(this.#roots, rec.conversationId, rec.id);
    }
    this.#tasks.set(rec.id, rec);
    if (rec.status === "terminal") this.#live.delete(rec.id);
    else this.#live.add(rec.id);
  }

  get(id: string): TaskRecord | undefined {
    return this.#tasks.get(id);
  }

  /** Non-terminal tasks, in creation order. */
  live(): TaskRecord[] {
    return [...this.#live].map((id) => this.#tasks.get(id) as TaskRecord);
  }

  all(): TaskRecord[] {
    return [...this.#tasks.values()];
  }

  ownedBy(id: string): string[] {
    const out = [...(this.#children.get(id) ?? [])];
    for (const conv of this.#ownedConvs.get(id) ?? []) out.push(...this.rootsOf(conv));
    return out;
  }

  conversationOwner(conversationId: string): string | null {
    return this.#convOwner.get(conversationId) ?? null;
  }

  rootsOf(conversationId: string): string[] {
    return [...(this.#roots.get(conversationId) ?? [])];
  }
}

/** The task that owns `task`: its parent, or its conversation's owner task, or null. */
export function ownerOf(lookup: TaskLookup, task: TaskRecord): string | null {
  return task.parentTaskId ?? lookup.conversationOwner(task.conversationId);
}

/** Owner chain of `id`, nearest first (excluding `id`). */
export function ancestors(lookup: TaskLookup, id: string): string[] {
  const out: string[] = [];
  const seen = new Set([id]);
  let current = lookup.get(id);
  while (current) {
    const owner = ownerOf(lookup, current);
    if (owner === null || seen.has(owner)) break;
    seen.add(owner);
    out.push(owner);
    current = lookup.get(owner);
  }
  return out;
}

/** Whether any task owned (directly) by `id` is not yet terminal. */
export function hasLiveOwned(lookup: TaskLookup, id: string): boolean {
  return lookup.ownedBy(id).some((child) => lookup.get(child)?.status !== "terminal");
}

/** `id` and everything it owns, transitively; descendants before their owners. */
export function subtreeBottomUp(lookup: TaskLookup, id: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const visit = (node: string): void => {
    if (seen.has(node)) return;
    seen.add(node);
    for (const child of lookup.ownedBy(node)) visit(child);
    out.push(node);
  };
  visit(id);
  return out;
}

/**
 * Throw `WaitRejected` unless `waiterId` may wait on `targets`: every target
 * must exist, and none may be the waiter, one of its owners, or reach either
 * through tasks it is itself waiting on (an owner implicitly waits on its
 * owned work, so either would deadlock).
 */
export function checkWaitTargets(lookup: TaskLookup, waiterId: string, targets: string[]): void {
  const forbidden = new Set([waiterId, ...ancestors(lookup, waiterId)]);
  const seen = new Set<string>();
  const queue: string[] = [];
  for (const id of targets) {
    if (!lookup.get(id)) throw new WaitRejected(waiterId, `unknown task ${id}`);
    queue.push(id);
  }
  while (queue.length > 0) {
    const id = queue.shift() as string;
    if (seen.has(id)) continue;
    seen.add(id);
    if (forbidden.has(id)) {
      const why = id === waiterId ? "waits on itself" : `waits on its owner ${id}`;
      throw new WaitRejected(
        waiterId,
        targets.includes(id) ? why : `wait cycle through ${id} (${why})`,
      );
    }
    const task = lookup.get(id);
    if (!task) continue;
    if (task.status === "waiting" && task.waiting) queue.push(...task.waiting.on);
    if (task.status === "completing") queue.push(...lookup.ownedBy(id));
  }
}
