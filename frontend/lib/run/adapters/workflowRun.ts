/**
 * Workflow runs -> RunNode trees (plan 003, U26).
 *
 * Two sources render through the same `RunTree`:
 * - an active engine run reads the container conversation's view (`view.tasks`: the `workflow_run` task and its
 *   `workflow_node` children, plus the run's `nodes` summary for skipped branches);
 * - a finished run (or one the engine view has not reached yet) reads the stored execution trace.
 * Waiting nodes carry their wake time and, from the `waiting-nodes` route, the ids the decision route needs.
 * Everything here is pure and reference-stable for unchanged input.
 */
import type { ConversationView, TaskView } from '@/lib/engine/types';
import type { WaitingNode, WorkflowLogDetail, WorkflowLogSummary } from '@/lib/api/workflow-logs';
import { isActive, normalizeStatus, type RunStatus } from '../status';
import { buildRunTree, type FlatRunNode, type RunNode } from '../tree';
import { viewToFlatNodes } from './engineView';

export const WORKFLOW_RUN_LABEL = 'Workflow run';
const RUN_KIND = 'workflow_run';
const NODE_KIND = 'workflow_node';

export interface WorkflowTreeResult {
  tree: RunNode[];
  /** Waiting-node info by `RunNode.id`, for the decide controls. */
  waiting: Map<string, WaitingNode>;
}

export interface WorkflowViewOptions {
  /** Display names by node id (from the stored trace or the canvas). */
  names?: Record<string, string>;
  waiting?: readonly WaitingNode[];
}

const EMPTY: WorkflowTreeResult = { tree: [], waiting: new Map() };

/** True when the view has a workflow run task (an older engine, or a run not yet reflected, has none). */
export function hasWorkflowRun(view: ConversationView | null | undefined): boolean {
  const tasks = view?.tasks;
  return !!tasks && Object.values(tasks).some((t) => t?.kind === RUN_KIND);
}

export function workflowViewToRunTree(view: ConversationView, opts: WorkflowViewOptions = {}, prev: readonly RunNode[] = []): WorkflowTreeResult {
  const tasks = (view.tasks ?? {}) as Record<string, TaskView>;
  if (!hasWorkflowRun(view)) return EMPTY;
  const names = opts.names ?? {};
  const waitingByNode = new Map<string, WaitingNode>();
  for (const w of opts.waiting ?? []) if (w.nodeId && !waitingByNode.has(w.nodeId)) waitingByNode.set(w.nodeId, w);

  const kept = new Set<string>();
  const flat: FlatRunNode[] = [];
  const waiting = new Map<string, WaitingNode>();
  for (const n of viewToFlatNodes(view)) {
    const task = tasks[n.id];
    if (!task && !(n.parentId && kept.has(n.parentId))) continue; // conversation-level run and subagents are not workflow nodes
    kept.add(n.id);
    const node: FlatRunNode = { ...n };
    if (task?.kind === RUN_KIND && task.parentId === null) {
      node.label = WORKFLOW_RUN_LABEL;
    } else if (task?.kind === NODE_KIND || (!task && n.kind === 'node')) {
      const nodeId = task?.label ?? n.label;
      node.label = names[nodeId] ?? nodeId;
      const w = task?.status === 'waiting' ? waitingByNode.get(nodeId) : undefined;
      if (w) {
        node.status = 'waiting';
        if (!node.waitUntil && w.wakeAt) node.waitUntil = w.wakeAt;
        waiting.set(n.id, w);
      }
    } else if (!task) {
      node.label = names[n.label] ?? n.label;
    }
    flat.push(node);
  }
  // The run root is parked only when something under it is and nothing is still working.
  const root = flat.find((n) => n.parentId === null && n.kind === 'run');
  if (root && root.status === 'running' && tasks[root.id]?.status === 'waiting') {
    const inner = flat.filter((n) => n.kind === 'node' && isActive(n.status));
    if (inner.length > 0 && inner.every((n) => n.status === 'waiting')) root.status = 'waiting';
  }
  return { tree: buildRunTree(flat, prev), waiting };
}

const sameNames = (a: Record<string, string> = {}, b: Record<string, string> = {}): boolean => {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k]);
};
const sameWaitingList = (a: readonly WaitingNode[] = [], b: readonly WaitingNode[] = []): boolean =>
  a.length === b.length &&
  a.every((w, i) => {
    const o = b[i];
    return w.approvalId === o.approvalId && w.nodeId === o.nodeId && w.wakeAt === o.wakeAt && w.taskId === o.taskId && w.conversationId === o.conversationId && w.status === o.status;
  });

/** Memoizing converter: an unchanged view, names and waiting list return the identical result; unchanged subtrees are reused. */
export function createWorkflowViewAdapter(): (view: ConversationView, opts?: WorkflowViewOptions) => WorkflowTreeResult {
  let lastView: ConversationView | null = null;
  let lastOpts: WorkflowViewOptions = {};
  let last: WorkflowTreeResult = EMPTY;
  return (view, opts = {}) => {
    if (view === lastView && sameNames(opts.names, lastOpts.names) && sameWaitingList(opts.waiting, lastOpts.waiting)) return last;
    const next = workflowViewToRunTree(view, opts, last.tree);
    lastView = view;
    lastOpts = opts;
    const sameTree = next.tree.length === last.tree.length && next.tree.every((n, i) => n === last.tree[i]);
    const sameWaiting = next.waiting.size === last.waiting.size && [...next.waiting].every(([k, w]) => last.waiting.get(k) === w || (last.waiting.has(k) && sameWaitingList([last.waiting.get(k)!], [w])));
    if (!(sameTree && sameWaiting)) last = sameTree ? { tree: last.tree, waiting: next.waiting } : next;
    return last;
  };
}

// ---- stored trace ------------------------------------------------------------------------------------

type Log = WorkflowLogSummary | WorkflowLogDetail;
const traceCache = new WeakMap<object, RunNode[]>();

export function nodeNamesOf(log: Log | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  const trace = log && 'execution_trace' in log ? log.execution_trace : undefined;
  for (const s of Array.isArray(trace) ? trace : []) if (s.node_id && s.node_name) out[s.node_id] = s.node_name;
  return out;
}

export function workflowTraceToRunTree(log: Log): RunNode[] {
  const hit = traceCache.get(log);
  if (hit) return hit;
  const trace = 'execution_trace' in log && Array.isArray(log.execution_trace) ? log.execution_trace : [];
  const root: RunNode = {
    id: log.id,
    kind: 'run',
    label: WORKFLOW_RUN_LABEL,
    status: normalizeStatus(log.status, 'workflow_log'),
    startedAt: log.started_at ?? null,
    endedAt: log.completed_at ?? null,
    children: trace.map((s, i) => {
      const node: RunNode = {
        // Engine traces have no step id and repeat a node id per attempt, so the position keeps ids unique.
        id: `${log.id}:${s.step_id ?? `${s.node_id ?? 'step'}#${i}`}`,
        kind: 'node',
        label: s.node_name || s.node_id || `Step ${i + 1}`,
        status: normalizeStatus(s.status, 'execution_trace'),
        startedAt: s.started_at ?? null,
        endedAt: s.completed_at ?? null,
        children: [],
      };
      if (s.error) node.error = s.error;
      return node;
    }),
  };
  if (typeof log.total_tokens === 'number' && log.total_tokens > 0) root.usage = { totalTokens: log.total_tokens };
  // The run-level error is shown by the page (a banner), not repeated on the root row.
  const tree = [root];
  traceCache.set(log, tree);
  return tree;
}

// ---- choosing the source ------------------------------------------------------------------------------

export interface SelectedWorkflowTree extends WorkflowTreeResult {
  source: 'engine' | 'trace';
}

/** Active runs read the engine view (once it shows the run); everything else reads the stored trace. */
export function selectWorkflowTree(input: {
  log: Log;
  view: ConversationView | null;
  waiting: readonly WaitingNode[];
  adapt?: (view: ConversationView, opts?: WorkflowViewOptions) => WorkflowTreeResult;
}): SelectedWorkflowTree {
  const { log, view, waiting } = input;
  const status: RunStatus = normalizeStatus(log.status, 'workflow_log');
  if (isActive(status) && view && hasWorkflowRun(view)) {
    const adapt = input.adapt ?? ((v, o) => workflowViewToRunTree(v, o));
    return { ...adapt(view, { names: nodeNamesOf(log), waiting }), source: 'engine' };
  }
  return { tree: workflowTraceToRunTree(log), waiting: new Map(), source: 'trace' };
}

/** `{ depth, label, status }` rows of a tree, depth first: what a user sees, without ids. */
export function projectRows(tree: readonly RunNode[], depth = 0): Array<{ depth: number; label: string; status: RunStatus }> {
  return tree.flatMap((n) => [{ depth, label: n.label, status: n.status }, ...projectRows(n.children, depth + 1)]);
}

/**
 * Overlay waiting-node info on a tree whose node ids ARE workflow node ids (the live stream tree): a running
 * node the route reports as parked becomes `waiting` with its wake time. Returns the input when nothing matches.
 */
export function overlayWaiting(tree: RunNode[], waiting: readonly WaitingNode[]): WorkflowTreeResult {
  const byNode = new Map<string, WaitingNode>();
  for (const w of waiting) if (w.nodeId && !byNode.has(w.nodeId)) byNode.set(w.nodeId, w);
  const map = new Map<string, WaitingNode>();
  if (byNode.size === 0) return { tree, waiting: map };
  const walk = (n: RunNode): RunNode => {
    const children = n.children.map(walk);
    const w = byNode.get(n.id);
    const parked = w && (n.status === 'running' || n.status === 'waiting');
    if (!parked && children.every((c, i) => c === n.children[i])) return n;
    if (!parked) return { ...n, children };
    map.set(n.id, w);
    const next: RunNode = { ...n, children, status: 'waiting' };
    if (w.wakeAt) next.waitUntil = w.wakeAt;
    return next;
  };
  const next = tree.map(walk);
  return { tree: next.every((n, i) => n === tree[i]) ? tree : next, waiting: map };
}
