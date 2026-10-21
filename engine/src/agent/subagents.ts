/**
 * Subagents (R13, R7, R20): a tool that delegates a task to a child
 * conversation and waits durably for its answer.
 *
 * `createSubagentTool({ name, ... })` defines a tool taking `{ prompt }`. The
 * tool task (`tool-task.ts`) authorizes the call as usual (hooks, then the
 * host policy, possibly an approval) and then, instead of executing it,
 * enters the `subagent` phase, which runs in three steps:
 *
 * 1. Find or create the child. In one commit: create a conversation owned by
 *    the tool task (same tenant and project; the parent's agent
 *    configuration with the tool's overrides, at depth + 1), submit the
 *    prompt to it with `requestId = subagent:<taskId>` and start its run,
 *    and park waiting on the run's generation. The child's conversation and
 *    submission ids go into the checkpoint in that same commit, so a rerun
 *    after a crash finds them and never creates a second child.
 * 2. Wait. While the child works, or waits on an approval, the tool task is
 *    `waiting` on the child's current answering task: nothing runs on its
 *    behalf. Each wake re-reads the child's submission, inside the commit
 *    that decides, and follows the run to its next generation.
 * 3. Answer. Once the child's submission settles, its committed answer
 *    becomes the tool result (`details` name the child conversation and
 *    submission). A child that did not answer yields an error result.
 *
 * A call whose child would be deeper than `maxDepth` (default 1, capped by the
 * engine's `maxSubagentDepth`) yields `limit_exceeded` and creates nothing.
 *
 * The child's root tasks are owned by the tool task, so aborting the parent's
 * run (or the tool call) aborts the child's tasks first, bottom-up, and the
 * parent finishes only after they are terminal.
 */
import { z } from "zod";
import type { JsonObject, JsonValue } from "../core/json.js";
import { type EntryRecord, MAX_PAGE_LIMIT, type SubmissionRecord } from "../storage/types.js";
import {
  type Committed,
  type TaskDefinition,
  type TaskScope,
  type TaskState,
  waiting,
} from "../tasks/define.js";
import { defineTool, type ToolDefinition, type ToolResult } from "../tools/registry.js";
import { agentResultDoc, COMPLETE_TASK_TOOL, metadataOutputSchema } from "./complete-task.js";
import type { ToolResultStatus } from "./context-builder.js";
import {
  CONTEXT_PREAMBLE_MAX_CHARS,
  composeChildPrompt,
  contextProblem,
  renderForkBlock,
} from "./context-seed.js";
import {
  GENERATION_TASK_KIND,
  type GenerationCheckpoint,
  type GenerationInput,
} from "./generation.js";
import { appendUserEntry, startRun } from "./inbox.js";
import { outputSchemaInstructions, schemaProblems } from "./json-schema-subset.js";
import { type AgentConfig, agentConfigDoc, applyAgentConfig, jsonValue } from "./prompt.js";
import {
  type SubmissionAnswer,
  type SubmissionInputRecord,
  type SubmissionUnanswered,
  settleSubmission,
  submissionResult,
  toSubmissionInput,
  toSubmissionResult,
} from "./submission.js";
import { rootConversationId, taintDoc } from "./taint.js";
import type { AgentDeps, ToolTaskCheckpoint, ToolTaskInput } from "./tool-task.js";

/** The tool task phase that runs a subagent call. */
export const SUBAGENT_PHASE = "subagent";

export const DEFAULT_SUBAGENT_MAX_DEPTH = 1;

export interface SubagentToolOptions {
  /** Tool name the model calls. */
  name: string;
  description?: string;
  /** Model hint for the child (overrides the parent's). */
  model?: string | null;
  /** Tools the child enables (overrides the parent's). */
  tools?: string[];
  /** The child's system prompt (overrides the parent's). */
  systemPrompt?: string;
  /** Deepest child this tool creates; the top-level agent is depth 0. Default 1. */
  maxDepth?: number;
  /**
   * A typed result (R16): the child ends by calling `complete_task` with a
   * value matching this JSON Schema subset (`json-schema-subset.ts`), and the
   * parent receives the validated payload. Absent: the child answers in text.
   */
  outputSchema?: JsonObject;
  /**
   * `"isolated"` (default): the child sees only its prompt and any per-call
   * `context`. `"fork"`: its first entry also holds a bounded narrative of the
   * parent's conversation (`context-seed.ts`).
   */
  mode?: SubagentMode;
}

export type SubagentMode = "isolated" | "fork";

/** What a subagent tool overrides in the child's configuration, and its depth limit. */
export interface SubagentSpec {
  model?: string | null;
  tools?: string[];
  systemPrompt?: string;
  outputSchema?: JsonObject;
  mode?: SubagentMode;
  maxDepth: number;
}

/**
 * Per-call context (R17): `brief` is text for the child (for example what the
 * sibling workers cover); `files` are conversation-workspace paths the child is
 * told to read (pointers, not content).
 */
export type SubagentContext = { brief?: string | undefined; files?: string[] | undefined };

export type SubagentArgs = {
  prompt: string;
  label?: string | undefined;
  context?: SubagentContext | undefined;
};

/** A label names one subagent call so a parent can steer it later (U21). */
const labelSchema = z
  .string()
  .trim()
  .regex(/^[A-Za-z0-9_-]{1,64}$/, "label must be 1-64 characters of A-Z, a-z, 0-9, _ or -");

export interface SubagentToolDefinition extends ToolDefinition<SubagentArgs> {
  readonly subagent: Readonly<SubagentSpec>;
}

/** The child of a subagent call, recorded in the tool task's checkpoint. */
export type SubagentLink = { conversationId: string; submissionId: string };

/** `details` of a subagent tool result. */
export type SubagentDetails = {
  conversationId: string;
  submissionId: string;
  depth: number;
  status: "done" | "unanswered";
  /** Why the child did not answer (`unanswered` only). */
  reason?: string;
  /** The validated typed result (a subagent with an `outputSchema`). */
  result?: JsonValue;
  /** The child's last plain-text answer when it never called `complete_task`. */
  rawText?: string;
  /** The child was asked once to call `complete_task` after answering in text. */
  nudged?: boolean;
};

export function createSubagentTool(options: SubagentToolOptions): SubagentToolDefinition {
  const maxDepth = options.maxDepth ?? DEFAULT_SUBAGENT_MAX_DEPTH;
  if (!Number.isSafeInteger(maxDepth) || maxDepth < 1) {
    throw new RangeError(`subagent ${options.name}: maxDepth must be a positive integer`);
  }
  if (options.tools !== undefined && !options.tools.every((t) => typeof t === "string")) {
    throw new TypeError(`subagent ${options.name}: tools must be tool names`);
  }
  if (options.outputSchema !== undefined) {
    const problems = schemaProblems(options.outputSchema);
    if (problems.length > 0) {
      throw new TypeError(`subagent ${options.name}: outputSchema: ${problems.join("; ")}`);
    }
  }
  if (options.mode !== undefined && options.mode !== "isolated" && options.mode !== "fork") {
    throw new TypeError(`subagent ${options.name}: mode must be "isolated" or "fork"`);
  }
  const spec: SubagentSpec = {
    maxDepth,
    ...(options.mode === undefined ? {} : { mode: options.mode }),
    ...(options.outputSchema === undefined
      ? {}
      : { outputSchema: structuredClone(options.outputSchema) }),
    ...(options.model === undefined ? {} : { model: options.model }),
    ...(options.tools === undefined ? {} : { tools: [...options.tools] }),
    ...(options.systemPrompt === undefined ? {} : { systemPrompt: options.systemPrompt }),
  };
  const base = defineTool<SubagentArgs>({
    name: options.name,
    description:
      options.description ??
      "Delegate a self-contained task to a subagent and get its final answer.",
    parameters: z.object({
      prompt: z.string().min(1).describe("The task for the subagent, with everything it needs."),
      label: labelSchema.optional().describe("A short name for this call, for later reference."),
      context: z
        .object({ brief: z.string().optional(), files: z.array(z.string()).optional() })
        .optional()
        .describe("Optional extra context for the subagent."),
    }),
    internal: true,
    enginePhase: {
      phase: SUBAGENT_PHASE,
      run: (deps, task, rt, w) => subagentPhase(deps, task, rt, w),
    },
    execute: () => ({
      content: `Subagent tool ${options.name} runs only inside the agent loop.`,
      isError: true,
    }),
  });
  return Object.freeze({ ...base, subagent: Object.freeze(spec) });
}

export function isSubagentTool(tool: ToolDefinition | undefined): tool is SubagentToolDefinition {
  return (
    tool !== undefined &&
    typeof (tool as Partial<SubagentToolDefinition>).subagent === "object" &&
    (tool as Partial<SubagentToolDefinition>).subagent !== null
  );
}

/**
 * Per-run overrides of one subagent tool, read from the parent's
 * `agent.metadata.subagents[toolName]`. Only the model, tools and system prompt
 * can be overridden; `maxDepth` (and any other key) is ignored, so metadata can
 * never loosen the depth limit.
 */
let overrideSchemaCache: ReturnType<typeof buildOverrideSchema> | undefined;
/** Built on first use: `context-seed.ts` and this file import each other through `compaction.ts`. */
const subagentOverrideSchema = () => (overrideSchemaCache ??= buildOverrideSchema());

const buildOverrideSchema = () =>
  z.object({
    model: z.string().nullable().optional(),
    tools: z.array(z.string()).optional(),
    systemPrompt: z.string().optional(),
    /** Static text placed first in every child's first entry. */
    contextPreamble: z.string().max(CONTEXT_PREAMBLE_MAX_CHARS).optional(),
    mode: z.enum(["isolated", "fork"]).optional(),
    outputSchema: z
      .record(z.string(), jsonValue)
      .superRefine((schema, ctx) => {
        for (const message of schemaProblems(schema)) ctx.addIssue({ code: "custom", message });
      })
      .optional(),
  });

export type SubagentOverride = z.infer<ReturnType<typeof buildOverrideSchema>>;

/** The override for `toolName` in the parent's metadata: none, valid, or malformed. */
export function subagentOverride(
  parent: AgentConfig,
  toolName: string,
): { ok: true; value: SubagentOverride | null } | { ok: false; reason: string } {
  const all = parent.metadata.subagents;
  if (all === undefined || all === null) return { ok: true, value: null };
  if (typeof all !== "object" || Array.isArray(all)) {
    return { ok: false, reason: "agent.metadata.subagents must be an object" };
  }
  if (!Object.hasOwn(all, toolName)) return { ok: true, value: null };
  const parsed = subagentOverrideSchema().safeParse(all[toolName]);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    return {
      ok: false,
      reason: `agent.metadata.subagents.${toolName}${issue?.path.length ? `.${issue.path.join(".")}` : ""}: ${issue?.message ?? "invalid"}`,
    };
  }
  return { ok: true, value: parsed.data };
}

/**
 * The child's configuration: the parent's, with the tool's overrides, one level
 * deeper. `override` (from the parent's metadata) wins over the static `spec`.
 */
export function childAgentConfig(
  parent: AgentConfig,
  spec: SubagentSpec,
  override: SubagentOverride | null = null,
): AgentConfig {
  const model = override?.model !== undefined ? override.model : spec.model;
  const tools = override?.tools ?? spec.tools;
  const systemPrompt = override?.systemPrompt ?? spec.systemPrompt;
  const outputSchema = (override?.outputSchema ?? spec.outputSchema) as JsonObject | undefined;
  if (outputSchema === undefined) {
    return applyAgentConfig(parent, {
      ...(model === undefined ? {} : { model }),
      ...(tools === undefined ? {} : { tools }),
      ...(systemPrompt === undefined ? {} : { systemPrompt }),
      depth: parent.depth + 1,
    });
  }
  // A typed result: the child gets `complete_task`, the schema in its prompt
  // and, for the tool to read, in its own metadata (the model cannot supply it).
  const childTools = [...(tools ?? parent.tools)];
  if (!childTools.includes(COMPLETE_TASK_TOOL)) childTools.push(COMPLETE_TASK_TOOL);
  return applyAgentConfig(parent, {
    ...(model === undefined ? {} : { model }),
    tools: childTools,
    systemPrompt: `${systemPrompt ?? parent.systemPrompt}\n\n${outputSchemaInstructions(outputSchema)}`,
    metadata: { ...parent.metadata, outputSchema },
    depth: parent.depth + 1,
  });
}

type State = TaskState<ToolTaskInput, ToolTaskCheckpoint>;
type Scope = TaskScope<ToolTaskCheckpoint>;
type Deps = Pick<AgentDeps, "tools" | "limits">;
type WriteResult = typeof import("./tool-task.js").writeToolResult;

const SETTLED = new Set(["done", "unanswered"]);

/** The `subagent` phase of a tool task. */
export async function subagentPhase(
  deps: Deps,
  task: State,
  rt: Scope,
  writeResult: WriteResult,
): Promise<Committed> {
  const { call } = task.input;
  const args = task.checkpoint?.arguments ?? {};
  const link = task.checkpoint?.subagent ?? null;
  const tool = deps.tools.get(call.name);
  const fail = (status: ToolResultStatus, result: ToolResult) =>
    rt.commit((tx) => writeResult(tx, task, status, result, 0));

  if (link === null) {
    if (!isSubagentTool(tool)) {
      return fail("error", {
        content: `Subagent tool "${call.name}" is no longer registered.`,
        isError: true,
      });
    }
    const parsed = tool.parameters.safeParse(args);
    if (!parsed.success) {
      return fail("invalid_arguments", {
        content: `Invalid arguments for ${call.name}: ${parsed.error.issues.map((i) => `${i.path.join(".") || "arguments"}: ${i.message}`).join("; ")}`,
        isError: true,
      });
    }
    // Caps live here, not in the schema: the schema is part of the prompt fingerprint.
    const tooBig = contextProblem(parsed.data.context);
    if (tooBig !== null) {
      return fail("invalid_arguments", {
        content: `Invalid arguments for ${call.name}: ${tooBig}`,
        isError: true,
      });
    }
    // The registry erases I/C (heterogeneous kinds); re-narrow to the generation kind.
    const generation = rt.runtime.registry.get(GENERATION_TASK_KIND) as unknown as
      | TaskDefinition<GenerationInput, GenerationCheckpoint>
      | undefined;
    if (!generation) throw new Error("the generation task kind is not registered");
    return spawnChild(task, rt, tool.subagent, parsed.data, args, generation, deps, writeResult);
  }
  return awaitChild(task, rt, link, writeResult);
}

/** Step 1: create the child, submit the prompt, start its run and wait on it. */
function spawnChild(
  task: State,
  rt: Scope,
  spec: SubagentSpec,
  call: SubagentArgs,
  args: JsonObject,
  generation: TaskDefinition<GenerationInput, GenerationCheckpoint>,
  deps: Deps,
  writeResult: WriteResult,
): Promise<Committed> {
  const maxDepth = Math.min(spec.maxDepth, deps.limits.maxSubagentDepth);
  const label = call.label;
  return rt.commit(async (tx, ops) => {
    const parent = await tx.getConversation(task.conversationId);
    if (!parent) throw new Error(`unknown conversation ${task.conversationId}`);
    const parentAgent = (await tx.doc(agentConfigDoc, task.conversationId)).value;
    const override = subagentOverride(parentAgent, task.input.call.name);
    if (!override.ok) {
      return writeResult(
        tx,
        task,
        "error",
        { content: `Invalid subagent configuration: ${override.reason}`, isError: true },
        0,
      );
    }
    const agent = childAgentConfig(parentAgent, spec, override.value);
    if (agent.depth > maxDepth) {
      return writeResult(
        tx,
        task,
        "limit_exceeded",
        {
          content: `Subagent depth limit reached: ${task.input.call.name} would run at depth ${agent.depth}, the maximum is ${maxDepth}. Do the task without delegating it.`,
          isError: true,
          details: { depth: agent.depth, maxDepth },
        },
        0,
      );
    }
    // The first entry: the prompt alone, unless context was asked for. Table
    // reads come before the commit's first write.
    const mode = override.value?.mode ?? spec.mode ?? "isolated";
    let fork: string | null = null;
    if (mode === "fork") {
      const entries = await parentEntries(tx, task.conversationId);
      const root = await rootConversationId(tx, parent);
      const tainted = (await tx.doc(taintDoc, root)).value.tainted;
      fork = renderForkBlock(entries, { tainted });
    }
    const prompt = composeChildPrompt(call.prompt, {
      preamble: override.value?.contextPreamble,
      fork,
      brief: call.context?.brief,
      files: call.context?.files,
    });
    if (prompt.length > deps.limits.maxEntryBytes / 2) {
      return writeResult(
        tx,
        task,
        "invalid_arguments",
        {
          content: `The subagent's task and context are too large (${prompt.length} characters).`,
          isError: true,
        },
        0,
      );
    }
    const child = ops.createConversation({
      tenantId: parent.tenantId,
      projectId: parent.projectId,
      ownerTaskId: task.id,
    });
    (await tx.doc(agentConfigDoc, child.id)).set(agent);
    const submissionId = tx.mintId("submission");
    const runId = tx.mintId("task");
    const input: SubmissionInputRecord = {
      content: prompt,
      type: "input",
      whenBusy: "followUp",
      untrusted: false,
    };
    tx.createSubmission({
      id: submissionId,
      conversationId: child.id,
      requestId: `subagent:${task.id}`,
      input: toSubmissionInput(input),
      status: "active",
      result: null,
      taskId: runId,
    });
    appendUserEntry(tx, child.id, submissionId, prompt, `subagent:${task.id}`);
    startRun(tx, ops, generation, child.id, submissionId, runId);
    return waiting({
      on: [runId],
      phase: SUBAGENT_PHASE,
      checkpoint: {
        arguments: args,
        subagent: { conversationId: child.id, submissionId },
        ...(label === undefined ? {} : { label }),
      },
    });
  });
}

/** The parent's entries, oldest first (read before the commit's first write). */
async function parentEntries(
  tx: Parameters<WriteResult>[0],
  conversationId: string,
): Promise<EntryRecord[]> {
  const out: EntryRecord[] = [];
  for (let cursor: string | null = null; ; ) {
    const page = await tx.listEntries(conversationId, { limit: MAX_PAGE_LIMIT, cursor });
    out.push(...page.items);
    cursor = page.nextCursor;
    if (cursor === null) return out;
  }
}

/** Steps 2 and 3: follow the child's run until its submission settles, then answer. */
function awaitChild(
  task: State,
  rt: Scope,
  link: SubagentLink,
  writeResult: WriteResult,
): Promise<Committed> {
  const nudge = task.checkpoint?.nudge ?? null;
  return rt.commit(async (tx, ops) => {
    const sub = await tx.getSubmission(nudge?.submissionId ?? link.submissionId);
    if (!sub) {
      return writeResult(
        tx,
        task,
        "error",
        { content: "The subagent's input is missing.", isError: true, details: { ...link } },
        0,
      );
    }
    if (SETTLED.has(sub.status)) return answer(tx, ops, rt, task, link, sub, writeResult);

    if (sub.status === "active" && sub.taskId !== null) {
      const current = ops.task(sub.taskId);
      if (current && current.status !== "terminal") {
        return waiting({ on: [current.id], phase: SUBAGENT_PHASE });
      }
      // The child's run ended without settling its input (aborted or
      // faulted): settle it here and report.
      const outcome = current?.outcome ?? null;
      const unanswered: SubmissionUnanswered = {
        reason: outcome?.type === "aborted" ? "aborted" : "faulted",
        message:
          outcome?.type === "aborted"
            ? outcome.reason
            : outcome?.type === "faulted"
              ? outcome.message
              : "the run ended without an answer",
        error: null,
      };
      settleSubmission(tx, sub, "unanswered", unanswered);
      return answer(
        tx,
        ops,
        rt,
        task,
        link,
        { ...sub, status: "unanswered", result: toSubmissionResult(unanswered) },
        writeResult,
      );
    }
    // Queued behind other input to the child: wait for its live runs.
    const live = rt.runtime
      .tasks({ conversationId: link.conversationId })
      .filter((t) => t.parentTaskId === null && t.status !== "terminal")
      .map((t) => t.id);
    if (live.length > 0) return waiting({ on: live, phase: SUBAGENT_PHASE });
    return writeResult(
      tx,
      task,
      "error",
      {
        content: "The subagent's input was never started.",
        isError: true,
        details: { ...link },
      },
      0,
    );
  });
}

/** The follow-up asked of a child that answered in text instead of calling `complete_task`. */
const COMPLETE_NUDGE =
  "You answered in text, but this task needs a typed result. Call the complete_task tool now with `result` matching the output schema.";

async function answer(
  tx: Parameters<WriteResult>[0],
  ops: Parameters<Parameters<Scope["commit"]>[0]>[1],
  rt: Scope,
  task: State,
  link: SubagentLink,
  sub: SubmissionRecord,
  writeResult: WriteResult,
) {
  const childAgent = (await tx.doc(agentConfigDoc, link.conversationId)).value;
  const depth = childAgent.depth;
  const typed = metadataOutputSchema(childAgent.metadata) !== null;
  const nudged = task.checkpoint?.nudge !== undefined;
  if (typed) {
    // The result was committed together with the child's `complete_task`
    // result, so it is here even if the child's run ended in a crash after it.
    const stored = (await tx.doc(agentResultDoc, link.conversationId)).value;
    if (stored !== null) {
      const details: SubagentDetails = {
        ...link,
        depth,
        status: "done",
        result: stored.result,
        ...(nudged ? { nudged: true } : {}),
      };
      return writeResult(
        tx,
        task,
        "ok",
        { content: JSON.stringify(stored.result), details: details as unknown as JsonValue },
        1,
      );
    }
    if (sub.status === "done") {
      const a = submissionResult(sub) as SubmissionAnswer | null;
      const rawText = typeof a?.text === "string" ? a.text : "";
      if (!nudged) {
        // One automatic follow-up asking the child to call complete_task.
        const generation = rt.runtime.registry.get(GENERATION_TASK_KIND) as unknown as
          | TaskDefinition<GenerationInput, GenerationCheckpoint>
          | undefined;
        if (!generation) throw new Error("the generation task kind is not registered");
        const submissionId = tx.mintId("submission");
        const runId = tx.mintId("task");
        const input: SubmissionInputRecord = {
          content: COMPLETE_NUDGE,
          type: "input",
          whenBusy: "followUp",
          untrusted: false,
        };
        tx.createSubmission({
          id: submissionId,
          conversationId: link.conversationId,
          requestId: `subagent:${task.id}:complete`,
          input: toSubmissionInput(input),
          status: "active",
          result: null,
          taskId: runId,
        });
        appendUserEntry(
          tx,
          link.conversationId,
          submissionId,
          COMPLETE_NUDGE,
          `subagent:${task.id}:complete`,
        );
        startRun(tx, ops, generation, link.conversationId, submissionId, runId);
        return waiting({
          on: [runId],
          phase: SUBAGENT_PHASE,
          checkpoint: {
            arguments: task.checkpoint?.arguments ?? {},
            subagent: link,
            ...(task.checkpoint?.label === undefined ? {} : { label: task.checkpoint.label }),
            nudge: { submissionId },
          },
        });
      }
      const details: SubagentDetails = {
        ...link,
        depth,
        status: "unanswered",
        reason: "no_result",
        rawText,
        nudged: true,
      };
      return writeResult(
        tx,
        task,
        "error",
        {
          content:
            "The subagent finished without calling complete_task, even after a reminder, so it returned no typed result.",
          isError: true,
          details: details as unknown as JsonValue,
        },
        1,
      );
    }
  }
  if (sub.status === "done") {
    const a = submissionResult(sub) as SubmissionAnswer | null;
    const details: SubagentDetails = { ...link, depth, status: "done" };
    const text = typeof a?.text === "string" ? a.text : "";
    return writeResult(
      tx,
      task,
      "ok",
      {
        content: text.length > 0 ? text : "The subagent finished without a text answer.",
        details: details as unknown as JsonValue,
      },
      1,
    );
  }
  const u = submissionResult(sub) as SubmissionUnanswered | null;
  const reason = typeof u?.reason === "string" ? u.reason : "unanswered";
  const details: SubagentDetails = { ...link, depth, status: "unanswered", reason };
  return writeResult(
    tx,
    task,
    reason === "aborted" ? "aborted" : "error",
    {
      content: `The subagent did not answer (${reason}): ${u?.message ?? ""}`.trim(),
      isError: true,
      details: details as unknown as JsonValue,
    },
    1,
  );
}
