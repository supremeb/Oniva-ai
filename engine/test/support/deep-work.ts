/**
 * Deep Work scenario support (plan 2026-10-15-002, U11): the agent config and
 * gateway manifest the backend really produces (recorded by
 * `backend/tests/internal/test_engine_deep_work_contract.py`), a stateful fake
 * of the `dw_*` gateway tools, stub worker tools, and the model script.
 *
 * The fixtures under `test/fixtures/deep-work/` are the contract: the fake
 * gateway serves the recorded `dw_*` manifest and the conversation is
 * configured with the recorded `compose_execute_config` output, so a backend
 * change to a tool schema or to the subagent overrides breaks the backend
 * contract test first and then this suite.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import type { AgentConfigInput } from "../../src/agent/prompt.js";
import { createSubagentTool } from "../../src/agent/subagents.js";
import type { JsonObject, JsonValue } from "../../src/core/json.js";
import type { ToolDefinition } from "../../src/tools/registry.js";
import { defineTool } from "../../src/tools/registry.js";
import type { FakeInvocation, FakeOutcome, FakeToolSpec } from "./fake-gateway.js";
import { type ModelRule, type ModelScript, respond } from "./scripted-model.js";

export const FIXTURE_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "fixtures",
  "deep-work",
);

export function readFixture<T = JsonValue>(name: string): T {
  return JSON.parse(readFileSync(join(FIXTURE_DIR, name), "utf8")) as T;
}

/** The backend's `compose_execute_config` output (system prompts replaced by placeholders). */
export function executeAgent(): AgentConfigInput {
  return readFixture<AgentConfigInput>("execute_agent.json");
}

// ---------------------------------------------------------------------------
// Holds: a handler or tool parks on a hold until the test releases it.
// ---------------------------------------------------------------------------

export class Hold {
  readonly entered: Promise<void>;
  readonly released: Promise<void>;
  #enter!: () => void;
  #release!: () => void;

  constructor() {
    this.entered = new Promise<void>((r) => {
      this.#enter = r;
    });
    this.released = new Promise<void>((r) => {
      this.#release = r;
    });
  }

  /** Called by the parked side. */
  async park(signal?: AbortSignal): Promise<void> {
    this.#enter();
    if (!signal) return this.released;
    const aborted = new Promise<never>((_, reject) => {
      if (signal.aborted) reject(signal.reason);
      signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    });
    aborted.catch(() => {});
    await Promise.race([this.released, aborted]);
  }

  release(): void {
    this.#release();
  }
}

/** Named hold points; `take(name)` hands out holds in the order the tests created them. */
export class Holds {
  readonly #queues = new Map<string, Hold[]>();

  add(name: string): Hold {
    const hold = new Hold();
    this.#queues.set(name, [...(this.#queues.get(name) ?? []), hold]);
    return hold;
  }

  take(name: string): Hold | undefined {
    return this.#queues.get(name)?.shift();
  }
}

// ---------------------------------------------------------------------------
// The fake dw_* gateway tools
// ---------------------------------------------------------------------------

interface RunState {
  rounds: { round: number; workers: { worker_id: string; sub_query: string; saved: boolean }[] }[];
  learnings: { worker_id: string; round: number; learnings: JsonObject[] }[];
  reflections: JsonObject[];
  sections: { heading: string; markdown: string }[];
  finalized: boolean;
}

const ok = (payload: JsonObject, details?: JsonObject): FakeOutcome => ({
  content: JSON.stringify(payload),
  ...(details ? { details } : {}),
});

const err = (message: string, code: string): FakeOutcome => ({
  content: message,
  isError: true,
  details: { error: code },
});

/**
 * The gateway tool specs: schemas, side effects and timeouts come from the
 * recorded backend manifest; the handlers are a small stateful stand-in for
 * the backend's run state (one run per root conversation).
 */
export function deepWorkGatewayTools(holds: Holds = new Holds()): FakeToolSpec[] {
  const manifest = readFixture<{ tools: Omit<FakeToolSpec, "handler">[] }>("dw_manifest.json");
  const runs = new Map<string, RunState>();
  const run = (call: FakeInvocation): RunState => {
    const key = call.conversationId ?? "";
    let state = runs.get(key);
    if (!state) {
      state = { rounds: [], learnings: [], reflections: [], sections: [], finalized: false };
      runs.set(key, state);
    }
    return state;
  };
  const open = (s: RunState, round: number) =>
    (s.rounds.find((r) => r.round === round)?.workers ?? []).filter((w) => !w.saved);

  const handlers: Record<string, (call: FakeInvocation) => FakeOutcome> = {
    dw_start_round(call) {
      const s = run(call);
      const round = call.args.round as number;
      const workers = (call.args.workers as { worker_id: string; sub_query: string }[]).map(
        (w) => ({ worker_id: w.worker_id, sub_query: w.sub_query, saved: false }),
      );
      if (round !== s.rounds.length + 1) {
        return err(`Round ${round} is out of order.`, "round_order");
      }
      s.rounds.push({ round, workers });
      return ok(
        { round, workers: workers.map(({ saved: _s, ...w }) => w) },
        { workspace: ["plan.md", "todo.md"] },
      );
    },
    dw_save_learnings(call) {
      const s = run(call);
      const id = call.args.worker_id as string;
      const entry = s.rounds.find((r) => r.workers.some((w) => w.worker_id === id));
      if (!entry) return err(`Worker ${id} is not registered.`, "unknown_worker");
      const learnings = call.args.learnings as JsonObject[];
      for (const w of entry.workers) if (w.worker_id === id) w.saved = true;
      s.learnings.push({ worker_id: id, round: entry.round, learnings });
      return ok(
        {
          worker_id: id,
          round: entry.round,
          learning_count: learnings.length,
          source_count: new Set(learnings.map((l) => l.source_url)).size,
          workers_still_open: open(s, entry.round).map((w) => w.worker_id),
        },
        { workspace: [`learnings/${id}.json`, "todo.md"] },
      );
    },
    dw_record_reflection(call) {
      const s = run(call);
      const round = call.args.round as number;
      s.reflections.push({ round, score: call.args.score as number, gaps: call.args.gaps ?? [] });
      const failed = (call.args.failed_workers as string[] | null | undefined) ?? [];
      for (const w of s.rounds.find((r) => r.round === round)?.workers ?? []) {
        if (failed.includes(w.worker_id)) w.saved = true;
      }
      return ok({
        round,
        recorded: true,
        ...(failed.length ? { failed_workers: failed } : {}),
        next: call.args.should_continue === true ? "dw_start_round" : "synthesis",
        workers_still_open: open(s, round).map((w) => w.worker_id),
      });
    },
    dw_write_section(call) {
      const s = run(call);
      const heading = call.args.heading as string;
      const at = s.sections.findIndex((x) => x.heading === heading);
      const section = { heading, markdown: call.args.markdown as string };
      if (at >= 0) s.sections[at] = section;
      else s.sections.push(section);
      return ok(
        { heading, replaced: at >= 0, sections: s.sections.map((x) => x.heading) },
        { workspace: ["report.md"] },
      );
    },
    dw_finalize(call) {
      const s = run(call);
      s.finalized = true;
      const report = s.sections.map((x) => `## ${x.heading}\n\n${x.markdown}`).join("\n\n");
      return ok({ report_length: report.length, artifacts: [] }, { workspace: ["report.md"] });
    },
    dw_get_learnings(call) {
      const s = run(call);
      const all = s.learnings.flatMap((l) =>
        l.learnings.map((x) => ({ worker_id: l.worker_id, ...x })),
      );
      return ok({ total: all.length, returned: all.length, learnings: all });
    },
    dw_get_report(call) {
      const s = run(call);
      const report = s.sections.map((x) => `## ${x.heading}\n\n${x.markdown}`).join("\n\n");
      return ok({ report, length: report.length, completed: s.finalized });
    },
  };

  return manifest.tools.map((spec) => {
    const handler = handlers[spec.name];
    if (!handler) throw new Error(`no fake handler for ${spec.name}`);
    return {
      ...spec,
      handler: async (call) => {
        await holds.take(spec.name)?.park();
        return handler(call);
      },
    };
  });
}

// ---------------------------------------------------------------------------
// Worker-side tools that do not go through the gateway, and the subagent tools
// ---------------------------------------------------------------------------

/** `web_search`, `browse_url` and `read_file` stand-ins; `web_search` parks on a hold named for the query. */
export function stubWorkerTools(holds: Holds): ToolDefinition<never>[] {
  const search = defineTool({
    name: "web_search",
    description: "Search the web",
    parameters: z.object({ query: z.string() }),
    replay: "safe",
    execute: async ({ query }, api) => {
      await holds.take(`web_search:${query}`)?.park(api.signal);
      if (query.includes("FAULT")) return { content: "search backend unavailable", isError: true };
      return { content: `results [${query}]` };
    },
  });
  const browse = defineTool({
    name: "browse_url",
    description: "Open a URL",
    parameters: z.object({ url: z.string() }),
    replay: "safe",
    execute: ({ url }) => ({ content: `page ${url}` }),
  });
  const read = defineTool({
    name: "read_file",
    description: "Read a workspace file",
    parameters: z.object({ path: z.string() }),
    replay: "safe",
    execute: ({ path }) => ({ content: `file ${path}` }),
  });
  return [search, browse, read] as unknown as ToolDefinition<never>[];
}

/** The two subagent tools of the execute config; overrides come from `metadata.subagents`. */
export function deepWorkSubagentTools() {
  return [
    createSubagentTool({ name: "research_worker" }),
    createSubagentTool({ name: "reflector" }),
  ];
}

// ---------------------------------------------------------------------------
// The model script
// ---------------------------------------------------------------------------

const call = (name: string, args: JsonObject) => ({
  type: "tool_call" as const,
  name,
  arguments: args,
});
const text = (t: string) => ({ type: "text" as const, text: t });

const learning = (id: string, n: number): JsonObject => ({
  fact: `Finding ${n} for ${id}`,
  source_url: `https://example.com/${id}/${n}`,
  confidence: 0.8,
  source_title: `Source ${id}`,
  source_type: "web",
  quote: `Quote ${n} for ${id}`,
});

/** Child rules for one worker: search, save, answer. */
function workerRules(id: string, query: string): ModelRule[] {
  return [
    {
      when: { role: "user", includes: `WORKER ${id}: ${query}` },
      respond: { steps: [call("web_search", { query })] },
    },
    {
      when: { role: "tool_result", tool: "web_search", includes: `[${query}]` },
      respond: {
        steps: [
          call("dw_save_learnings", {
            worker_id: id,
            learnings: [learning(id, 1), learning(id, 2)],
          }),
        ],
      },
    },
    {
      when: { role: "tool_result", tool: "dw_save_learnings", includes: `"worker_id":"${id}"` },
      respond: {
        steps: [
          text(
            JSON.stringify({
              summary: `saved ${id}`,
              learning_count: 2,
              source_count: 2,
              top_findings: [`Finding 1 for ${id}`],
              sources: [1, 2].map((n) => ({
                title: `Source ${id}`,
                url: `https://example.com/${id}/${n}`,
                type: "web",
              })),
            }),
          ),
        ],
      },
    },
  ];
}

/**
 * Scripted answers for the Deep Work shape: an orchestrator that starts round
 * 1 with `w1`, reflects (adding `gap-1`), runs round 2, reflects again (stop),
 * writes two sections and finalizes. `PARALLEL` starts round 1 with `w1` and
 * `w2` together in one assistant message. `FAULT` makes `w1`'s search fail so
 * its child ends in a model error. `ORCH-ERROR` makes the orchestrator's
 * request after the reflection fail.
 */
export const DEEP_WORK_SCRIPT: ModelScript = {
  rules: [
    // --- orchestrator ---------------------------------------------------
    {
      when: { role: "user", includes: "Run this approved research plan" },
      respond: {
        steps: [
          call("dw_start_round", {
            round: 1,
            workers: [{ worker_id: "w1", sub_query: "q-w1" }],
          }),
        ],
      },
    },
    {
      when: { role: "user", includes: "Run the parallel plan" },
      respond: {
        steps: [
          call("dw_start_round", {
            round: 1,
            workers: [
              { worker_id: "w1", sub_query: "q-w1" },
              { worker_id: "w2", sub_query: "q-w2" },
            ],
          }),
        ],
      },
    },
    {
      when: { role: "user", includes: "Run the failing plan" },
      respond: {
        steps: [
          call("dw_start_round", {
            round: 1,
            workers: [{ worker_id: "w1", sub_query: "q-FAULT" }],
          }),
        ],
      },
    },
    {
      when: { role: "tool_result", tool: "dw_start_round", includes: '"worker_id":"w2"' },
      respond: {
        steps: [
          call("research_worker", { prompt: "WORKER w1: q-w1" }),
          call("research_worker", { prompt: "WORKER w2: q-w2" }),
        ],
      },
    },
    {
      when: { role: "tool_result", tool: "dw_start_round", includes: "q-FAULT" },
      respond: { steps: [call("research_worker", { prompt: "WORKER w1: q-FAULT" })] },
    },
    {
      when: { role: "tool_result", tool: "dw_start_round", includes: '"round":1' },
      respond: { steps: [call("research_worker", { prompt: "WORKER w1: q-w1" })] },
    },
    {
      when: { role: "tool_result", tool: "dw_start_round", includes: '"round":2' },
      respond: { steps: [call("research_worker", { prompt: "WORKER gap-1: q-gap-1" })] },
    },
    {
      when: { role: "tool_result", tool: "research_worker", includes: "saved gap-1" },
      respond: { steps: [call("reflector", { prompt: "REFLECT round 2" })] },
    },
    {
      when: { role: "tool_result", tool: "research_worker", includes: "saved w" },
      respond: { steps: [call("reflector", { prompt: "REFLECT round 1" })] },
    },
    {
      // A worker fault (the call came back as an error result): the round continues.
      when: { role: "tool_result", tool: "research_worker", includes: "did not answer" },
      respond: { steps: [call("reflector", { prompt: "REFLECT faulted round 1" })] },
    },
    {
      when: { role: "tool_result", tool: "reflector", includes: "reflected faulted" },
      respond: {
        steps: [{ type: "error", kind: "invalid_request", message: "orchestrator failed" }],
      },
    },
    {
      when: { role: "tool_result", tool: "reflector", includes: "reflected 1" },
      respond: {
        steps: [
          call("dw_start_round", {
            round: 2,
            workers: [{ worker_id: "gap-1", sub_query: "q-gap-1" }],
          }),
        ],
      },
    },
    {
      when: { role: "tool_result", tool: "reflector", includes: "reflected 2" },
      respond: {
        steps: [
          call("dw_write_section", {
            heading: "Findings",
            markdown: "Findings from w1 and gap-1.",
          }),
        ],
      },
    },
    {
      when: { role: "tool_result", tool: "dw_write_section", includes: '"sections":["Findings"]' },
      respond: {
        steps: [call("dw_write_section", { heading: "Gaps closed", markdown: "gap-1 closed." })],
      },
    },
    {
      when: { role: "tool_result", tool: "dw_write_section" },
      respond: { steps: [call("dw_finalize", {})] },
    },
    {
      when: { role: "tool_result", tool: "dw_finalize" },
      respond: { steps: [text("Deep Work complete.")] },
    },
    // --- reflector ------------------------------------------------------
    {
      when: { role: "user", includes: "REFLECT" },
      respond: { steps: [call("dw_get_learnings", {})] },
    },
    {
      when: { role: "tool_result", tool: "dw_get_learnings", includes: "gap-1" },
      respond: {
        steps: [
          call("dw_record_reflection", {
            round: 2,
            score: 88,
            gaps: [],
            should_continue: false,
          }),
        ],
      },
    },
    {
      when: { role: "tool_result", tool: "dw_get_learnings", includes: "w1" },
      respond: {
        steps: [
          call("dw_record_reflection", {
            round: 1,
            score: 55,
            gaps: ["gap-1: the second angle"],
            should_continue: true,
            gap_tasks: [{ worker_id: "gap-1", sub_query: "q-gap-1" }],
          }),
        ],
      },
    },
    {
      // A faulted round has no learnings at all.
      when: { role: "tool_result", tool: "dw_get_learnings" },
      respond: {
        steps: [
          call("dw_record_reflection", {
            round: 1,
            score: 0,
            gaps: ["w1 failed"],
            should_continue: false,
            failed_workers: ["w1"],
          }),
        ],
      },
    },
    {
      when: {
        role: "tool_result",
        tool: "dw_record_reflection",
        includes: '"failed_workers":["w1"]',
      },
      respond: {
        steps: [
          text(
            JSON.stringify({
              score: 0,
              gaps: ["w1 failed"],
              round: 1,
              continuing: false,
              note: "reflected faulted",
            }),
          ),
        ],
      },
    },
    {
      when: { role: "tool_result", tool: "dw_record_reflection", includes: '"round":2' },
      respond: {
        steps: [
          text(
            JSON.stringify({
              score: 88,
              gaps: [],
              round: 2,
              continuing: false,
              note: "reflected 2",
            }),
          ),
        ],
      },
    },
    {
      when: { role: "tool_result", tool: "dw_record_reflection", includes: '"round":1' },
      respond: {
        steps: [
          text(
            JSON.stringify({
              score: 55,
              gaps: ["gap-1: the second angle"],
              round: 1,
              continuing: true,
              note: "reflected 1",
            }),
          ),
        ],
      },
    },
    // --- workers --------------------------------------------------------
    {
      when: { role: "tool_result", tool: "web_search", includes: "unavailable" },
      respond: { steps: [{ type: "error", kind: "invalid_request", message: "worker failed" }] },
    },
    ...workerRules("w1", "q-w1"),
    ...workerRules("w2", "q-w2"),
    ...workerRules("gap-1", "q-gap-1"),
    ...workerRules("w1", "q-FAULT"),
  ],
};

/** A `FakeProvider` response function for the script. */
export const scripted = (request: Parameters<typeof respond>[1]) =>
  respond(DEEP_WORK_SCRIPT, request);
