import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  GraphError,
  type GraphNode,
  MAX_FANOUT_ITEMS,
  MAX_PARALLEL,
  MAX_SUBWORKFLOW_DEPTH,
  MAX_WORKFLOW_NODES,
  type NodeStates,
  resolveGraph,
  resolveTarget,
  validateGraph,
  type WorkflowGraph,
} from "../../src/workflow/graph.js";

const POLICY = { maxRetries: 0, backoffMs: 0, timeoutMs: 0, onError: "stop" } as const;

const node = (id: string, deps: GraphNode["deps"] = [], handles?: string[]): GraphNode => ({
  id,
  type: "code",
  deps,
  policy: { ...POLICY },
  ...(handles ? { handles } : {}),
});

const graph = (...nodes: GraphNode[]): WorkflowGraph => ({ nodes });

function codeOf(raw: unknown): string | undefined {
  try {
    validateGraph(raw);
  } catch (err) {
    if (err instanceof GraphError) return err.code;
    throw err;
  }
  return undefined;
}

describe("workflow graph validation (U12)", () => {
  it("the node limit equals limits.json and the backend's MAX_WORKFLOW_NODES", () => {
    const limits = JSON.parse(
      readFileSync(
        join(
          dirname(fileURLToPath(import.meta.url)),
          "..",
          "..",
          "contracts",
          "workflow-nodes",
          "limits.json",
        ),
        "utf8",
      ),
    ) as { maxNodes: number };
    expect(MAX_WORKFLOW_NODES).toBe(limits.maxNodes);
    expect(MAX_WORKFLOW_NODES).toBe(95);
  });

  it("accepts 95 nodes and rejects 96", () => {
    const chain = (n: number) =>
      graph(
        ...Array.from({ length: n }, (_, i) => node(`n${i}`, i ? [{ from: `n${i - 1}` }] : [])),
      );
    expect(codeOf(chain(95))).toBeUndefined();
    expect(codeOf(chain(96))).toBe("too_many_nodes");
  });

  it("rejects a cycle, a self loop, an unknown dependency, an unknown handle and duplicates", () => {
    expect(codeOf(graph(node("a", [{ from: "b" }]), node("b", [{ from: "a" }])))).toBe("cycle");
    expect(codeOf(graph(node("a", [{ from: "a" }])))).toBe("cycle");
    expect(codeOf(graph(node("a", [{ from: "ghost" }])))).toBe("unknown_dependency");
    expect(
      codeOf(graph(node("c", [], ["true"]), node("a", [{ from: "c", handle: "maybe" }]))),
    ).toBe("unknown_handle");
    expect(codeOf(graph(node("c"), node("a", [{ from: "c", handle: "true" }])))).toBe(
      "unknown_handle",
    );
    expect(codeOf(graph(node("a"), node("a")))).toBe("duplicate_node");
  });

  it("rejects an empty graph and a malformed policy", () => {
    expect(codeOf({ nodes: [] })).toBe("invalid_graph");
    expect(
      codeOf({ nodes: [{ id: "a", type: "code", deps: [], policy: { maxRetries: -1 } }] }),
    ).toBe("invalid_graph");
    expect(codeOf(null)).toBe("invalid_graph");
  });
});

describe("workflow graph scheduling (U12)", () => {
  const settled = (status: "success" | "failed" | "skipped", extra = {}): NodeStates[string] => ({
    status,
    ...extra,
  });

  it("starts entry nodes, then a join only after every source settled", () => {
    const g = graph(node("a"), node("b"), node("j", [{ from: "a" }, { from: "b" }]));
    expect(resolveGraph(g, {}).ready).toEqual(["a", "b"]);
    const half: NodeStates = { a: settled("success"), b: { status: "running" } };
    expect(resolveGraph(g, half).ready).toEqual([]);
    const both: NodeStates = { a: settled("success"), b: settled("success") };
    expect(resolveGraph(g, both).ready).toEqual(["j"]);
  });

  it("skips the branch that was not selected and its descendants; a merge with one live input runs", () => {
    const g = graph(
      node("c", [], ["true", "false"]),
      node("t", [{ from: "c", handle: "true" }]),
      node("t2", [{ from: "t" }]),
      node("f", [{ from: "c", handle: "false" }]),
      node("m", [{ from: "t2" }, { from: "f" }]),
    );
    const r = resolveGraph(g, { c: settled("success", { handles: ["false"] }) });
    expect(r.states.t?.status).toBe("skipped");
    expect(r.states.t2?.status).toBe("skipped");
    expect(r.ready).toEqual(["f"]);
    const r2 = resolveGraph(g, { ...r.states, f: settled("success") });
    expect(r2.ready).toEqual(["m"]);
  });

  it("a node whose in-edges are all dead is skipped; an uncaught failure kills its edges, a caught one does not", () => {
    const g = graph(node("a"), node("b", [{ from: "a" }]));
    expect(resolveGraph(g, { a: settled("failed") }).states.b?.status).toBe("skipped");
    expect(resolveGraph(g, { a: settled("failed", { continued: true }) }).ready).toEqual(["b"]);
    expect(resolveGraph(g, { a: settled("skipped") }).states.b?.status).toBe("skipped");
  });
});

describe("nested graphs: sub-graphs and subworkflows (U34)", () => {
  const chain = (n: number, prefix = "n"): WorkflowGraph =>
    graph(
      ...Array.from({ length: n }, (_, i) =>
        node(`${prefix}${i}`, i ? [{ from: `${prefix}${i - 1}` }] : []),
      ),
    );

  it("the fan-out limits equal limits.json", () => {
    const limits = JSON.parse(
      readFileSync(
        join(
          dirname(fileURLToPath(import.meta.url)),
          "..",
          "..",
          "contracts",
          "workflow-nodes",
          "limits.json",
        ),
        "utf8",
      ),
    ) as { maxParallel: number; maxFanoutItems: number; maxSubworkflowDepth: number };
    expect(MAX_PARALLEL).toBe(limits.maxParallel);
    expect(MAX_FANOUT_ITEMS).toBe(limits.maxFanoutItems);
    expect(MAX_SUBWORKFLOW_DEPTH).toBe(limits.maxSubworkflowDepth);
  });

  it("accepts a marked fan-out node with a sub-graph and a referenced workflow", () => {
    const g: WorkflowGraph = {
      nodes: [{ ...node("loop"), mayFanOut: true }],
      subgraphs: { body: chain(2, "b") },
      workflows: { "wf-1": chain(2, "w") },
    };
    expect(validateGraph(g)).toEqual(g);
    expect(resolveTarget(g, { subgraph: "body" })).toEqual(chain(2, "b"));
    expect(resolveTarget(g, { workflowId: "wf-1" })).toEqual(chain(2, "w"));
    expect(resolveTarget(g, { subgraph: "nope" })).toBeNull();
    expect(resolveTarget(g, { workflowId: "constructor" })).toBeNull();
  });

  it("counts the nodes of every nested graph toward the limit", () => {
    const fits: WorkflowGraph = { ...chain(50), subgraphs: { body: chain(45, "b") } };
    expect(codeOf(fits)).toBeUndefined();
    const over: WorkflowGraph = { ...chain(50), subgraphs: { body: chain(46, "b") } };
    expect(codeOf(over)).toBe("too_many_nodes");
    const viaWorkflow: WorkflowGraph = { ...chain(50), workflows: { w: chain(46, "b") } };
    expect(codeOf(viaWorkflow)).toBe("too_many_nodes");
  });

  it("checks a nested graph like the top one: a cycle or unknown dependency inside is rejected", () => {
    const cyc = graph(node("a", [{ from: "b" }]), node("b", [{ from: "a" }]));
    expect(codeOf({ ...chain(1), subgraphs: { body: cyc } })).toBe("cycle");
    const bad = graph(node("a", [{ from: "ghost" }]));
    expect(codeOf({ ...chain(1), workflows: { w: bad } })).toBe("unknown_dependency");
  });

  it("allows subworkflows nested three deep and rejects four", () => {
    const nest = (levels: number): WorkflowGraph => {
      let g = chain(1, "leaf");
      for (let i = levels; i >= 1; i--) g = { ...chain(1, `l${i}`), workflows: { [`wf-${i}`]: g } };
      return g;
    };
    // The top graph plus `levels` referenced workflows: wf-1 at depth 1 ... wf-levels.
    expect(codeOf(nest(3))).toBeUndefined();
    expect(codeOf(nest(4))).toBe("subworkflow_too_deep");
  });

  it("rejects a workflow that references itself, directly or through another", () => {
    const self: WorkflowGraph = {
      ...chain(1),
      workflows: { a: { ...chain(1, "x"), workflows: { a: chain(1, "y") } } },
    };
    expect(codeOf(self)).toBe("recursive_reference");
    const loop: WorkflowGraph = {
      ...chain(1),
      workflows: {
        a: {
          ...chain(1, "x"),
          workflows: { b: { ...chain(1, "y"), workflows: { a: chain(1, "z") } } },
        },
      },
    };
    expect(codeOf(loop)).toBe("recursive_reference");
  });

  it("refuses pathologically deep nesting before parsing it", () => {
    let g: Record<string, unknown> = { nodes: [] };
    for (let i = 0; i < 40; i++) g = { nodes: [], subgraphs: { s: g } };
    expect(codeOf(g)).toBe("invalid_graph");
  });
});
