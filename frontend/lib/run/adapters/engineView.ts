/**
 * Engine view -> RunNode trees (plan 003, U23).
 *
 * - Host tasks (`view.tasks`: workflow runs and nodes) become `run`/`node` nodes along `parentId`.
 * - The conversation's own run (`view.run`) is the root `run` node when there is one; subagents
 *   (`view.subagents`) and `interrupted` tool results hang under it.
 * - `resuming` is a `run_resumed` entry with no later progress entry; `interrupted` is a tool
 *   result of status `interrupted`; a finished task whose phase is `skipped` is `skipped`.
 * Pure and tolerant: older engines without `tasks`, and malformed leaves, never throw.
 * `createEngineViewAdapter()` memoizes on the view and reuses unchanged subtrees.
 */
import type { ConversationView, EntryRecord, SubagentView, TaskView, UsageTally } from '@/lib/engine/types';
import { engineTaskStatus, type RunStatus } from '../status';
import { buildRunTree, type FlatRunNode, type RunNode, type RunUsage } from '../tree';

const isoOf = (ms: unknown): string | null => (typeof ms === 'number' && Number.isFinite(ms) ? new Date(ms).toISOString() : null);

function usageOf(t: UsageTally | undefined): RunUsage | undefined {
  if (!t || typeof t.inputTokens !== 'number') return undefined;
  return { inputTokens: t.inputTokens, outputTokens: t.outputTokens, costUsd: t.costUsd ?? null };
}

const obj = (v: unknown): Record<string, unknown> | null =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

/** True when the newest `run_resumed` entry has no progress entry after it. */
function isResuming(entries: readonly EntryRecord[]): boolean {
  let at = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].kind === 'run_resumed') {
      at = i;
      break;
    }
  }
  if (at < 0) return false;
  return !entries.slice(at + 1).some((e) => e.kind !== 'run_resumed');
}

function taskNode(id: string, t: TaskView): FlatRunNode {
  let status: RunStatus = engineTaskStatus(t.status);
  if (t.status === 'done' && t.phase === 'skipped') status = 'skipped';
  const node: FlatRunNode = {
    id,
    parentId: t.parentId,
    kind: t.parentId === null ? 'run' : 'node',
    label: t.label || t.kind,
    status,
    startedAt: t.startedAt ?? null,
    endedAt: t.endedAt ?? null,
  };
  if (status === 'waiting' && t.waitUntil) node.waitUntil = t.waitUntil;
  if (t.error) node.error = t.error;
  return node;
}

function subagentNode(id: string, s: SubagentView, parentId: string): FlatRunNode {
  const node: FlatRunNode = {
    id,
    parentId,
    kind: 'subagent',
    label: s.recent?.length ? `Subagent: ${s.recent[s.recent.length - 1]}` : 'Subagent',
    status: engineTaskStatus(s.status === 'failed' ? 'faulted' : s.status),
    startedAt: s.startedAt ?? null,
    endedAt: s.endedAt ?? null,
  };
  const usage = usageOf(s.usage);
  if (usage) node.usage = usage;
  return node;
}

function runStatusOf(view: ConversationView): RunStatus {
  if (view.run?.active) return isResuming(view.entries) ? 'resuming' : 'running';
  const last = [...view.entries].reverse().find((e) => e.kind === 'run_error' || e.kind === 'run_end' || e.kind === 'assistant');
  return last?.kind === 'run_error' ? 'failed' : 'completed';
}

/** Flatten a view into parented nodes (roots have `parentId: null`). */
export function viewToFlatNodes(view: ConversationView): FlatRunNode[] {
  const out: FlatRunNode[] = [];
  const tasks = obj(view.tasks) as Record<string, TaskView> | null;
  if (tasks) for (const [id, t] of Object.entries(tasks)) if (obj(t)) out.push(taskNode(id, t));

  const subagents = obj(view.subagents) as Record<string, SubagentView> | null;
  const interrupted = (view.entries ?? []).filter((e) => {
    const p = obj(e.payload);
    return e.kind === 'tool_result' && p?.status === 'interrupted';
  });
  const hasOwnRun = !!view.run?.runId;
  if (hasOwnRun && (view.run?.active || Object.keys(subagents ?? {}).length || interrupted.length)) {
    const rootId = `run:${view.run!.runId}`;
    const root: FlatRunNode = {
      id: rootId,
      parentId: null,
      kind: 'run',
      label: 'Run',
      status: runStatusOf(view),
      startedAt: view.run!.startedAt ?? null,
      endedAt: null,
    };
    const usage = usageOf(view.usage?.total);
    if (usage) root.usage = usage;
    out.unshift(root);
    for (const [id, s] of Object.entries(subagents ?? {})) if (obj(s)) out.push(subagentNode(id, s, rootId));
    for (const e of interrupted) {
      const p = obj(e.payload)!;
      out.push({
        id: `tool:${String(p.toolCallId ?? e.id)}`,
        parentId: rootId,
        kind: 'tool',
        label: typeof p.toolName === 'string' ? p.toolName : 'Tool call',
        status: 'interrupted',
        startedAt: isoOf(e.createdAt),
        endedAt: isoOf(e.createdAt),
      });
    }
  } else {
    // No conversation run to hang them on: subagents stand alone.
    for (const [id, s] of Object.entries(subagents ?? {})) if (obj(s)) out.push(subagentNode(id, s, ''));
  }
  return out;
}

/** One-shot conversion (no memoization). */
export function engineViewToRunTree(view: ConversationView): RunNode[] {
  return buildRunTree(viewToFlatNodes(view));
}

/** Memoizing converter: the same view object, or a view that changes nothing visible, returns the same tree. */
export function createEngineViewAdapter(): (view: ConversationView) => RunNode[] {
  let lastView: ConversationView | null = null;
  let lastTree: RunNode[] = [];
  return (view) => {
    if (view === lastView) return lastTree;
    const next = buildRunTree(viewToFlatNodes(view), lastTree);
    const same = next.length === lastTree.length && next.every((n, i) => n === lastTree[i]);
    lastView = view;
    if (!same) lastTree = next;
    return lastTree;
  };
}
