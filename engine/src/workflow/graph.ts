/**
 * The compiled workflow graph and its scheduling rules (plan 2026-10-17-003,
 * U12, KTD3/KTD5). The backend compiles the React Flow spec once, at start;
 * the engine schedules only from this form and holds no node semantics.
 *
 * Rules (pure functions, no I/O):
 * - A node is ready when every in-edge's source has settled and at least one
 *   in-edge is live. Entry nodes (no in-edges) are ready at start.
 * - An edge is dead when its source was skipped or blocked, when it names a
 *   handle the source did not select, or when its source failed without its
 *   own `onError: "continue"` (a failure nobody caught). A node whose
 *   in-edges are all dead becomes `skipped`, and that propagates.
 * - Merge nodes therefore wait only on live inputs: a dead input never blocks
 *   them, but it must have settled first.
 *
 * Validation rejects a cycle, a duplicate id, a dependency on an unknown node,
 * a handle the source node never declares and a node count over
 * {@link MAX_WORKFLOW_NODES}.
 */
import { z } from "zod";
import { nodePolicySchema } from "./contract.js";
import type { NodePolicy, NodeStatus } from "./types.js";

/**
 * The most nodes one graph may hold. Mirrors `maxNodes` in
 * `contracts/workflow-nodes/limits.json` and `MAX_WORKFLOW_NODES` in the
 * backend (a test pins the equality).
 */
export const MAX_WORKFLOW_NODES = 95;

/** An in-edge: the source node and, for a branch, the handle it must have selected. */
export interface GraphDep {
  from: string;
  /** Absent or null: the edge is live whenever its source ran. */
  handle?: string | null | undefined;
}

export interface GraphNode {
  id: string;
  type: string;
  deps: GraphDep[];
  policy: NodePolicy;
  /** The handles this node may select (a condition's `true`/`false`). Absent: none. */
  handles?: string[] | undefined;
}

export interface WorkflowGraph {
  nodes: GraphNode[];
}

export type ErrorMode = "stop" | "continue";

export type GraphErrorCode =
  | "invalid_graph"
  | "too_many_nodes"
  | "duplicate_node"
  | "unknown_dependency"
  | "unknown_handle"
  | "cycle";

/** A graph the engine refuses to run; `code` is stable. */
export class GraphError extends Error {
  constructor(
    readonly code: GraphErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "GraphError";
  }
}

const graphSchema = z.strictObject({
  nodes: z
    .array(
      z.strictObject({
        id: z.string().min(1),
        type: z.string().min(1),
        deps: z.array(
          z.strictObject({ from: z.string().min(1), handle: z.string().nullable().optional() }),
        ),
        policy: nodePolicySchema,
        handles: z.array(z.string()).optional(),
      }),
    )
    .min(1),
});

/**
 * Validate a graph taken from untrusted input. Returns it typed or throws
 * {@link GraphError}.
 */
export function validateGraph(raw: unknown): WorkflowGraph {
  if (
    typeof raw === "object" &&
    raw !== null &&
    Array.isArray((raw as { nodes?: unknown }).nodes)
  ) {
    const count = (raw as { nodes: unknown[] }).nodes.length;
    if (count > MAX_WORKFLOW_NODES) {
      throw new GraphError(
        "too_many_nodes",
        `a graph holds at most ${MAX_WORKFLOW_NODES} nodes, got ${count}`,
      );
    }
  }
  const parsed = graphSchema.safeParse(raw);
  if (!parsed.success) throw new GraphError("invalid_graph", z.prettifyError(parsed.error));
  const graph = parsed.data as WorkflowGraph;

  const byId = new Map<string, GraphNode>();
  for (const node of graph.nodes) {
    if (byId.has(node.id)) throw new GraphError("duplicate_node", `duplicate node ${node.id}`);
    byId.set(node.id, node);
  }
  for (const node of graph.nodes) {
    for (const dep of node.deps) {
      const source = byId.get(dep.from);
      if (!source) {
        throw new GraphError(
          "unknown_dependency",
          `node ${node.id} depends on unknown node ${dep.from}`,
        );
      }
      if (dep.handle != null && !(source.handles ?? []).includes(dep.handle)) {
        throw new GraphError(
          "unknown_handle",
          `node ${node.id} uses handle ${dep.handle}, which ${dep.from} does not declare`,
        );
      }
    }
  }
  assertAcyclic(graph);
  return graph;
}

/** Kahn's algorithm; a leftover node is on or behind a cycle. */
function assertAcyclic(graph: WorkflowGraph): void {
  const pending = new Map(graph.nodes.map((n) => [n.id, n.deps.length]));
  const dependents = new Map<string, string[]>();
  for (const node of graph.nodes) {
    for (const dep of node.deps) {
      dependents.set(dep.from, [...(dependents.get(dep.from) ?? []), node.id]);
    }
  }
  const queue = [...pending].filter(([, n]) => n === 0).map(([id]) => id);
  let seen = 0;
  for (let id = queue.shift(); id !== undefined; id = queue.shift()) {
    seen += 1;
    for (const next of dependents.get(id) ?? []) {
      const left = (pending.get(next) ?? 0) - 1;
      pending.set(next, left);
      if (left === 0) queue.push(next);
    }
  }
  if (seen !== graph.nodes.length) {
    const stuck = [...pending].filter(([, n]) => n > 0).map(([id]) => id);
    throw new GraphError("cycle", `the graph has a cycle through ${stuck.slice(0, 5).join(", ")}`);
  }
}

/** Where a node is. `pending` and `running` are unsettled. */
export type NodeRunStatus = "pending" | "running" | NodeStatus;

export interface NodeRunState {
  status: NodeRunStatus;
  /** The handles the node selected (branch nodes). */
  handles?: string[] | undefined;
  /** A failure the node's own `onError: "continue"` caught. */
  continued?: true | undefined;
}

export type NodeStates = Record<string, NodeRunState>;

export const isSettled = (s: NodeRunState | undefined): boolean =>
  s !== undefined && s.status !== "pending" && s.status !== "running";

/** Is the edge `dep` live given its source's settled state? */
function liveEdge(dep: GraphDep, source: NodeRunState): boolean {
  if (source.status === "skipped" || source.status === "blocked") return false;
  if (source.status === "failed" && source.continued !== true) return false;
  if (dep.handle == null) return true;
  return source.handles?.includes(dep.handle) === true;
}

export interface Resolution {
  /** Node states with skips propagated. */
  states: NodeStates;
  /** Nodes to start now, in graph order. */
  ready: string[];
}

/**
 * Propagate skips and find the ready set. `pending` nodes whose in-edges are
 * all dead become `skipped`, repeatedly, until nothing changes.
 */
export function resolveGraph(graph: WorkflowGraph, current: NodeStates): Resolution {
  const states: NodeStates = {};
  for (const node of graph.nodes)
    states[node.id] = { ...(current[node.id] ?? { status: "pending" }) };
  const ready: string[] = [];
  let changed = true;
  while (changed) {
    changed = false;
    ready.length = 0;
    for (const node of graph.nodes) {
      const state = states[node.id] as NodeRunState;
      if (state.status !== "pending") continue;
      if (node.deps.length === 0) {
        ready.push(node.id);
        continue;
      }
      const sources = node.deps.map((d) => states[d.from] as NodeRunState);
      if (!sources.every(isSettled)) continue;
      if (node.deps.some((d, i) => liveEdge(d, sources[i] as NodeRunState))) {
        ready.push(node.id);
      } else {
        states[node.id] = { status: "skipped" };
        changed = true;
      }
    }
  }
  return { states, ready: [...ready] };
}
