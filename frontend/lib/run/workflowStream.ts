/**
 * Live workflow stream (`POST /workflows/execute-stream`) -> per-node run state (plan 003, U26).
 *
 * Events (engine path, U17): `started` (first; names the execution), `progress` (a node started), `node_complete`, `node_skipped`, `error`, `complete`.
 * The older `node_started` is accepted. Pure: each event returns a new state (or the same object when ignored).
 */
import { normalizeStatus, RUN_STATUSES, type RunStatus } from './status';
import type { RunNode } from './tree';

export interface LiveNodeState {
  status: RunStatus;
  output?: unknown;
  error?: string;
  elapsedMs?: number;
}

export interface LiveRun {
  nodes: Record<string, LiveNodeState>;
  /** Node ids in the order they first appeared. */
  order: string[];
  activeNodeId: string | null;
  /** The run's execution id, from the stream's first event (`started`) or its `complete`; null before. */
  executionId: string | null;
  /** The run's final status once `complete` or `error` arrived. */
  ended: RunStatus | null;
  error?: string;
}

export interface StreamEvent {
  type: string;
  [key: string]: unknown;
}

export const emptyLiveRun = (): LiveRun => ({ nodes: {}, order: [], activeNodeId: null, executionId: null, ended: null });

const isRunStatus = (v: unknown): v is RunStatus => typeof v === 'string' && (RUN_STATUSES as readonly string[]).includes(v);

/** `success` is the orchestrator's word for completed; the rest go through the shared mapping. */
function nodeResultStatus(v: unknown): RunStatus {
  if (v === 'halted') return 'waiting';
  if (typeof v === 'string' && ['success', 'complete', 'completed'].includes(v)) return 'completed';
  return isRunStatus(v) || typeof v === 'string' ? normalizeStatus(v, 'execute-stream') : 'completed';
}

function put(run: LiveRun, id: string, node: LiveNodeState, active: string | null | undefined = undefined): LiveRun {
  return {
    ...run,
    nodes: { ...run.nodes, [id]: { ...run.nodes[id], ...node } },
    order: run.order.includes(id) ? run.order : [...run.order, id],
    activeNodeId: active === undefined ? run.activeNodeId : active,
  };
}

export function applyStreamEvent(run: LiveRun, event: StreamEvent): LiveRun {
  const nodeId = typeof event.node_id === 'string' ? event.node_id : null;
  if (typeof event.execution_id === 'string' && run.executionId === null) run = { ...run, executionId: event.execution_id };
  switch (event.type) {
    case 'started':
      return run;
    case 'node_started':
      return nodeId ? put(run, nodeId, { status: 'running' }, nodeId) : run;
    case 'progress': {
      if (!nodeId) return run;
      const status = event.status === 'waiting' ? 'waiting' : 'running';
      return put(run, nodeId, { status }, nodeId);
    }
    case 'node_skipped':
      return nodeId ? put(run, nodeId, { status: 'skipped' }, run.activeNodeId === nodeId ? null : undefined) : run;
    case 'node_complete': {
      if (!nodeId) return run;
      const result = (event.result ?? {}) as { status?: unknown; output?: unknown; error?: unknown; elapsed_ms?: unknown };
      const node: LiveNodeState = { status: nodeResultStatus(result.status) };
      if (result.output !== undefined && result.output !== null) node.output = result.output;
      if (typeof result.error === 'string' && result.error) node.error = result.error;
      if (typeof result.elapsed_ms === 'number') node.elapsedMs = result.elapsed_ms;
      return put(run, nodeId, node, run.activeNodeId === nodeId ? null : undefined);
    }
    case 'error': {
      const message = typeof event.error === 'string' ? event.error : 'Workflow failed';
      const next = nodeId ? put(run, nodeId, { status: 'failed', error: message }) : run;
      return { ...next, activeNodeId: null, ended: 'failed', error: message };
    }
    case 'complete': {
      let next: LiveRun = run;
      const results = (event.results ?? {}) as Record<string, { status?: unknown; output?: unknown; error?: unknown } | undefined>;
      for (const [id, r] of Object.entries(results)) {
        if (next.nodes[id]) continue;
        next = put(next, id, { status: nodeResultStatus(r?.status), ...(typeof r?.error === 'string' && r.error ? { error: r.error } : {}) });
      }
      const status = event.status;
      const ended: RunStatus = status === 'failed' || status === 'cancelled' || status === 'interrupted' ? status : 'completed';
      return { ...next, activeNodeId: null, ended };
    }
    default:
      return run;
  }
}

/** The run as a `RunNode` tree for the test-run panel. Empty until the first event. */
export function liveRunToTree(run: LiveRun, names: Record<string, string> = {}): RunNode[] {
  if (run.order.length === 0 && !run.ended) return [];
  const children: RunNode[] = run.order.map((id) => {
    const n = run.nodes[id];
    const node: RunNode = { id, kind: 'node', label: names[id] ?? id, status: n.status, startedAt: null, endedAt: null, children: [] };
    if (n.error) node.error = n.error;
    return node;
  });
  const root: RunNode = {
    id: 'live-run',
    kind: 'run',
    label: 'Workflow run',
    status: run.ended ?? 'running',
    startedAt: null,
    endedAt: null,
    children,
  };
  if (run.error) root.error = run.error;
  return [root];
}
