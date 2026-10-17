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
 * {@link MAX_WORKFLOW_NODES}. Nested graphs (`subgraphs` for iteration bodies,
 * `workflows` for referenced workflows, U34) are checked the same way; the node
 * count spans all of them, `workflows` nest at most {@link MAX_SUBWORKFLOW_DEPTH}
 * deep and a workflow cannot contain itself.
 */
import { z } from "zod";
import { MAX_FANOUT_ITEMS, MAX_PARALLEL, nodePolicySchema } from "./contract.js";
import type { NodePolicy, NodeStatus } from "./types.js";

/**
 * The most nodes one graph may hold. Mirrors `maxNodes` in
 * `contracts/workflow-nodes/limits.json` and `MAX_WORKFLOW_NODES` in the
 * backend (a test pins the equality).
 */
export const MAX_WORKFLOW_NODES = 95;

/** Mirrors `maxSubworkflowDepth` in `limits.json`: how deep `workflows` may nest. */
export const MAX_SUBWORKFLOW_DEPTH = 3;

export { MAX_FANOUT_ITEMS, MAX_PARALLEL };

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
  /**
   * The node may answer `prepare` with `fanout` (iteration, subworkflow, batch).
   * The run then spawns a `workflow_gate` next to it so it learns the node parked (U34).
   */
  mayFanOut?: boolean | undefined;
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  /**
   * Iteration bodies by the name a `fanout` target gives (`{ subgraph }`). Each is
   * a self-contained graph; its nodes count toward {@link MAX_WORKFLOW_NODES}.
   */
  subgraphs?: Record<string, WorkflowGraph> | undefined;
  /**
   * Referenced workflows by id (`{ workflowId }` targets), resolved by the backend at
   * start. Nesting is capped at {@link MAX_SUBWORKFLOW_DEPTH} and a workflow cannot
   * appear inside itself.
   */
  workflows?: Record<string, WorkflowGraph> | undefined;
}

export type ErrorMode = "stop" | "continue";

export type GraphErrorCode =
  | "invalid_graph"
  | "too_many_nodes"
  | "duplicate_node"
  | "unknown_dependency"
  | "unknown_handle"
  | "cycle"
  | "subworkflow_too_deep"
  | "recursive_reference";

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

const graphSchema: z.ZodType<WorkflowGraph> = z.lazy(() =>
  z.strictObject({
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
          mayFanOut: z.boolean().optional(),
        }),
      )
      .min(1),
    subgraphs: z.record(z.string(), graphSchema).optional(),
    workflows: z.record(z.string(), graphSchema).optional(),
  }),
) as unknown as z.ZodType<WorkflowGraph>;

/** Nesting of `subgraphs`/`workflows` the raw input may have before parsing is refused. */
const MAX_RAW_NESTING = 16;

/** Every node of a raw graph and its nested graphs, bounded in depth. */
function countRawNodes(raw: unknown, level = 0): number {
  if (typeof raw !== "object" || raw === null) return 0;
  if (level > MAX_RAW_NESTING) {
    throw new GraphError("invalid_graph", `graphs nest deeper than ${MAX_RAW_NESTING} levels`);
  }
  const g = raw as { nodes?: unknown; subgraphs?: unknown; workflows?: unknown };
  let total = Array.isArray(g.nodes) ? g.nodes.length : 0;
  for (const nested of [g.subgraphs, g.workflows]) {
    if (typeof nested !== "object" || nested === null || Array.isArray(nested)) continue;
    for (const child of Object.values(nested)) total += countRawNodes(child, level + 1);
  }
  return total;
}

/**
 * Validate a graph taken from untrusted input. Returns it typed or throws
 * {@link GraphError}. The node limit counts the nodes of every nested graph.
 */
export function validateGraph(raw: unknown): WorkflowGraph {
  const count = countRawNodes(raw);
  if (count > MAX_WORKFLOW_NODES) {
    throw new GraphError(
      "too_many_nodes",
      `a graph holds at most ${MAX_WORKFLOW_NODES} nodes, got ${count}`,
    );
  }
  const parsed = graphSchema.safeParse(raw);
  if (!parsed.success) throw new GraphError("invalid_graph", z.prettifyError(parsed.error));
  checkGraph(parsed.data, 0, []);
  return parsed.data;
}

/** The structural checks of one graph level, then of its nested graphs. */
function checkGraph(graph: WorkflowGraph, depth: number, ancestors: string[]): void {
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
  for (const sub of Object.values(graph.subgraphs ?? {})) checkGraph(sub, depth, ancestors);
  for (const [id, workflow] of Object.entries(graph.workflows ?? {})) {
    if (ancestors.includes(id)) {
      throw new GraphError("recursive_reference", `workflow ${id} references itself`);
    }
    if (depth + 1 > MAX_SUBWORKFLOW_DEPTH) {
      throw new GraphError(
        "subworkflow_too_deep",
        `subworkflows nest at most ${MAX_SUBWORKFLOW_DEPTH} levels deep`,
      );
    }
    checkGraph(workflow, depth + 1, [...ancestors, id]);
  }
}

/** The graph a fan-out target runs per item, or null when the graph does not hold it. */
export function resolveTarget(
  graph: WorkflowGraph,
  target: { subgraph: string } | { workflowId: string },
): WorkflowGraph | null {
  if ("subgraph" in target) {
    const sub = graph.subgraphs;
    return sub && Object.hasOwn(sub, target.subgraph) ? (sub[target.subgraph] ?? null) : null;
  }
  const wf = graph.workflows;
  return wf && Object.hasOwn(wf, target.workflowId) ? (wf[target.workflowId] ?? null) : null;
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
