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
import type { SubmissionRecord } from "../storage/types.js";
import {
  type Committed,
  type TaskDefinition,
  type TaskScope,
  type TaskState,
  waiting,
} from "../tasks/define.js";
import { defineTool, type ToolDefinition, type ToolResult } from "../tools/registry.js";
import type { ToolResultStatus } from "./context-builder.js";
import {
  GENERATION_TASK_KIND,
  type GenerationCheckpoint,
  type GenerationInput,
} from "./generation.js";
import { appendUserEntry, startRun } from "./inbox.js";
import { type AgentConfig, agentConfigDoc, applyAgentConfig } from "./prompt.js";
import {
  type SubmissionAnswer,
  type SubmissionInputRecord,
  type SubmissionUnanswered,
  settleSubmission,
} from "./submission.js";
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
}

/** What a subagent tool overrides in the child's configuration, and its depth limit. */
export interface SubagentSpec {
  model?: string | null;
  tools?: string[];
  systemPrompt?: string;
  maxDepth: number;
}

export type SubagentArgs = { prompt: string };

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
};

export function createSubagentTool(options: SubagentToolOptions): SubagentToolDefinition {
  const maxDepth = options.maxDepth ?? DEFAULT_SUBAGENT_MAX_DEPTH;
  if (!Number.isSafeInteger(maxDepth) || maxDepth < 1) {
    throw new RangeError(`subagent ${options.name}: maxDepth must be a positive integer`);
  }
  if (options.tools !== undefined && !options.tools.every((t) => typeof t === "string")) {
    throw new TypeError(`subagent ${options.name}: tools must be tool names`);
  }
  const spec: SubagentSpec = {
    maxDepth,
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
    }),
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

/** The child's configuration: the parent's, with the tool's overrides, one level deeper. */
export function childAgentConfig(parent: AgentConfig, spec: SubagentSpec): AgentConfig {
  return applyAgentConfig(parent, {
    ...(spec.model === undefined ? {} : { model: spec.model }),
    ...(spec.tools === undefined ? {} : { tools: spec.tools }),
    ...(spec.systemPrompt === undefined ? {} : { systemPrompt: spec.systemPrompt }),
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
        content: `Invalid arguments for ${call.name}: a non-empty "prompt" is required.`,
        isError: true,
      });
    }
    const generation = rt.runtime.registry.get(GENERATION_TASK_KIND) as unknown as
      | TaskDefinition<GenerationInput, GenerationCheckpoint>
      | undefined;
    if (!generation) throw new Error("the generation task kind is not registered");
    return spawnChild(
      task,
      rt,
      tool.subagent,
      parsed.data.prompt,
      args,
      generation,
      deps,
      writeResult,
    );
  }
  return awaitChild(task, rt, link, writeResult);
}

/** Step 1: create the child, submit the prompt, start its run and wait on it. */
function spawnChild(
  task: State,
  rt: Scope,
  spec: SubagentSpec,
  prompt: string,
  args: JsonObject,
  generation: TaskDefinition<GenerationInput, GenerationCheckpoint>,
  deps: Deps,
  writeResult: WriteResult,
): Promise<Committed> {
  const maxDepth = Math.min(spec.maxDepth, deps.limits.maxSubagentDepth);
  return rt.commit(async (tx, ops) => {
    const parent = await tx.getConversation(task.conversationId);
    if (!parent) throw new Error(`unknown conversation ${task.conversationId}`);
    const agent = childAgentConfig((await tx.doc(agentConfigDoc, task.conversationId)).value, spec);
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
    const child = ops.createConversation({
      tenantId: parent.tenantId,
      projectId: parent.projectId,
      ownerTaskId: task.id,
    });
    (await tx.doc(agentConfigDoc, child.id)).set(agent);
    const submissionId = tx.mintId("submission");
    const runId = tx.mintId("task");
    const input: SubmissionInputRecord = { content: prompt, type: "input", whenBusy: "followUp" };
    tx.createSubmission({
      id: submissionId,
      conversationId: child.id,
      requestId: `subagent:${task.id}`,
      input: input as unknown as JsonValue,
      status: "active",
      result: null,
      taskId: runId,
    });
    appendUserEntry(tx, child.id, submissionId, prompt);
    startRun(tx, ops, generation, child.id, submissionId, runId);
    return waiting({
      on: [runId],
      phase: SUBAGENT_PHASE,
      checkpoint: { arguments: args, subagent: { conversationId: child.id, submissionId } },
    });
  });
}

/** Steps 2 and 3: follow the child's run until its submission settles, then answer. */
function awaitChild(
  task: State,
  rt: Scope,
  link: SubagentLink,
  writeResult: WriteResult,
): Promise<Committed> {
  return rt.commit(async (tx, ops) => {
    const sub = await tx.getSubmission(link.submissionId);
    if (!sub) {
      return writeResult(
        tx,
        task,
        "error",
        { content: "The subagent's input is missing.", isError: true, details: { ...link } },
        0,
      );
    }
    if (SETTLED.has(sub.status)) return answer(tx, task, link, sub, writeResult);

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
        task,
        link,
        { ...sub, status: "unanswered", result: unanswered as unknown as JsonValue },
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

async function answer(
  tx: Parameters<WriteResult>[0],
  task: State,
  link: SubagentLink,
  sub: SubmissionRecord,
  writeResult: WriteResult,
) {
  const depth = (await tx.doc(agentConfigDoc, link.conversationId)).value.depth;
  if (sub.status === "done") {
    const a = sub.result as unknown as SubmissionAnswer;
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
  const u = sub.result as unknown as SubmissionUnanswered;
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
