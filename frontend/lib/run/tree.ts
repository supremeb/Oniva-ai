/** One recursive run-tree shape for Fleet jobs, workflow runs, Deep Work and engine tasks (plan 003, U23). */
import type { RunStatus } from './status';

export type RunNodeKind = 'run' | 'subagent' | 'worker' | 'node' | 'tool';

export interface RunUsage {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  costUsd?: number | null;
}

export interface RunNode {
  id: string;
  kind: RunNodeKind;
  label: string;
  status: RunStatus;
  /** ISO time, or null when it has not started. */
  startedAt: string | null;
  endedAt: string | null;
  usage?: RunUsage;
  /** ISO wake time of a `waiting` node that has one. */
  waitUntil?: string;
  error?: string;
  children: RunNode[];
}

/** A node before its children are attached. `parentId: null` marks a root. */
export type FlatRunNode = Omit<RunNode, 'children'> & { parentId: string | null };

function sameUsage(a?: RunUsage, b?: RunUsage): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.inputTokens === b.inputTokens &&
    a.outputTokens === b.outputTokens &&
    a.totalTokens === b.totalTokens &&
    a.costUsd === b.costUsd
  );
}

function sameNode(prev: RunNode, next: RunNode): boolean {
  return (
    prev.id === next.id &&
    prev.kind === next.kind &&
    prev.label === next.label &&
    prev.status === next.status &&
    prev.startedAt === next.startedAt &&
    prev.endedAt === next.endedAt &&
    prev.waitUntil === next.waitUntil &&
    prev.error === next.error &&
    sameUsage(prev.usage, next.usage) &&
    prev.children.length === next.children.length &&
    prev.children.every((c, i) => c === next.children[i])
  );
}

/**
 * Reuse `prev` objects wherever a subtree is unchanged, so memoised React
 * rows do not re-render and an unchanged input gives the identical tree.
 */
export function reuseTree(prev: RunNode | undefined, next: RunNode): RunNode {
  if (!prev) return next;
  const prevById = new Map(prev.children.map((c) => [c.id, c]));
  const children = next.children.map((c) => reuseTree(prevById.get(c.id), c));
  const merged: RunNode = { ...next, children };
  return sameNode(prev, merged) ? prev : merged;
}

/**
 * Assemble flat nodes into trees. Order among siblings is input order. A node
 * whose parent is absent becomes a root (a partial view must not drop it), and
 * a cycle is broken by treating its members as roots. `prev` roots (by id) are
 * reused where unchanged.
 */
export function buildRunTree(flat: readonly FlatRunNode[], prev: readonly RunNode[] = []): RunNode[] {
  const ids = new Set(flat.map((n) => n.id));
  const byParent = new Map<string | null, FlatRunNode[]>();
  const rootOf = (n: FlatRunNode): string | null => (n.parentId !== null && ids.has(n.parentId) && n.parentId !== n.id ? n.parentId : null);
  for (const n of flat) {
    const key = rootOf(n);
    const list = byParent.get(key);
    if (list) list.push(n);
    else byParent.set(key, [n]);
  }
  const placed = new Set<string>();
  const build = (n: FlatRunNode): RunNode => {
    placed.add(n.id);
    const { parentId: _p, ...rest } = n;
    const children = (byParent.get(n.id) ?? []).filter((c) => !placed.has(c.id)).map(build);
    return { ...rest, children };
  };
  const roots = (byParent.get(null) ?? []).map(build);
  // Members of a parent cycle never reach a root: surface them rather than lose them.
  for (const n of flat) if (!placed.has(n.id)) roots.push(build(n));
  const prevById = new Map(prev.map((r) => [r.id, r]));
  return roots.map((r) => reuseTree(prevById.get(r.id), r));
}
