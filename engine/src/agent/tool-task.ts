/**
 * The tool task (R6, R8, R9, R20): one durable task per tool call, owned by
 * the generation task that received the call.
 *
 * Phases:
 * - `start`: wait for earlier calls this one is ordered after (`sequential`
 *   tools), check the tool is registered and enabled, validate the
 *   arguments, run the `beforeTool` hooks (which may rewrite them),
 *   re-validate, then ask `HostPolicy.authorizeTool` about the final
 *   arguments. `block` (or a throwing policy) commits a `blocked` result;
 *   `allow` commits the intent: a transition to `execute` whose checkpoint
 *   holds the final arguments. `requireApproval` commits an approval request
 *   together with `waiting({ signal: "approval", until: expiresAt })`, to
 *   resume in `approved` with the final arguments as the checkpoint.
 * - `approved` (U9, R10): woken by `decideApproval`'s signal or by the
 *   deadline. `approve` commits the intent exactly as `allow` does; `deny`
 *   commits a `denied` result; the deadline commits `approval_expired` and
 *   closes the request. The wait holds no running task.
 * - `execute`: run the tool, stream its partial output (throttled) to the
 *   task's `agent.tool_output` document, run the `afterTool` hooks and commit
 *   the `tool_result` entry together with the task's `done`.
 *
 * Recovery: entering `execute` without having committed the intent in this
 * process means a previous process died between intent and result. A
 * replay-safe tool runs again (same idempotency key, next attempt number).
 * Otherwise the tool's `reconcile` may supply the stored outcome; failing
 * that the model gets an `interrupted` result that warns the effect may have
 * happened and carries the output committed so far. The tool is not run
 * again.
 *
 * - `answered` (U10, R10): a tool flagged `humanInput` (`ask_user`) never
 *   executes. After authorization it commits a question together with
 *   `waiting({ signal: "answer", until: expiresAt })`; the answer commits an
 *   `ok` result whose content is the answer text, the deadline a
 *   `question_expired` result. An abort closes the question `cancelled`.
 *
 * A subagent tool (`subagents.ts`) leaves `start` / `approved` for the
 * `subagent` phase instead of `execute`: it runs a child conversation the
 * task owns and waits for its answer without a running invocation.
 *
 * Result statuses: a failed result whose `details.error` is `timeout` or
 * `lease_lost` (the tool gateway's failure codes) gets that status; any other
 * failure is `error`.
 *
 * A task aborted while awaiting approval closes its request as `cancelled`.
 */
import type { Clock } from "../core/clock.js";
import type { Context } from "../core/context.js";
import { errorText } from "../core/errors.js";
import { type JsonObject, jsonByteLength } from "../core/json.js";
import type { EngineLimits } from "../core/limits.js";
import type { ModelRegistry } from "../models/stream.js";
import type { Session } from "../session/session.js";
import type { Transaction } from "../session/transaction.js";
import { type EntryRecord, MAX_PAGE_LIMIT } from "../storage/types.js";
import {
  type Committed,
  defineTask,
  done,
  next,
  type TaskDefinition,
  type TaskScope,
  type TaskState,
  type Transition,
  waiting,
} from "../tasks/define.js";
import type {
  ToolApi,
  ToolConversation,
  ToolDefinition,
  ToolReconcileApi,
  ToolRegistry,
  ToolResult,
} from "../tools/registry.js";
import {
  APPROVAL_SIGNAL,
  approvalOf,
  approvalsDoc,
  closeApproval,
  decisionFromSignal,
  requestApproval,
  resultApproval,
} from "./approvals.js";
import { resolveCompactionSettings } from "./compaction.js";
import {
  ENTRY_KINDS,
  type ToolResultPayload,
  type ToolResultStatus,
  toEntryPayload,
} from "./context-builder.js";
import { type AgentErrorReporter, type HookRunner, isToolResult } from "./hooks.js";
import { maybeOffload, type OffloadStore } from "./offload.js";
import { authorize, type HostPolicy } from "./policy.js";
import {
  appendOutput,
  ProgressWriter,
  type ToolOutputState,
  toolOutputDoc,
  toolOutputIntervalMs,
} from "./progress.js";
import { type AgentConfig, agentConfigDoc, type CompactionOverrides } from "./prompt.js";
import {
  ANSWER_SIGNAL,
  answerText,
  closeQuestion,
  isAnswerSignal,
  questionSpecSchema,
  questionsDoc,
  requestQuestion,
} from "./questions.js";
import { SUBAGENT_PHASE, type SubagentLink, subagentPhase } from "./subagents.js";
import { markTainted, rootConversationId, taintOf, taintSummary, toolTaints } from "./taint.js";

export const TOOL_TASK_KIND = "agent.tool";

/** The general phase of a tool with an `enginePhase` handler. */
export const ENGINE_PHASE = "engine";

/** Everything the agent's task handlers need from the harness. */
export interface AgentDeps {
  session: Session;
  models: ModelRegistry;
  tools: ToolRegistry;
  policy: HostPolicy;
  hooks: HookRunner;
  clock: Clock;
  limits: Readonly<EngineLimits>;
  progressIntervalMs: number;
  report: AgentErrorReporter;
  /**
   * Tool tasks whose intent this process committed and whose `execute` phase
   * has not started yet. Process memory on purpose: after a restart it is
   * empty, which is how `execute` recognises an interrupted call.
   */
  freshIntents: Set<string>;
  /** Expiry of an `ask_user` question in ms after it is asked; null for none. */
  questionTtlMs: number | null;
  /**
   * Where Tier 0 puts a large tool result (`offload.ts`). Absent: nothing is
   * offloaded, whatever the conversation's settings say.
   */
  offload?: OffloadStore | undefined;
  /** Harness-level compaction settings, under a conversation's `agent.compaction`. */
  compactionDefaults?: CompactionOverrides | undefined;
}

export type ToolCallInput = {
  id: string;
  name: string;
  arguments: JsonObject;
  /** Raw argument text when the model's arguments were not a JSON object. */
  invalidArguments: string | null;
};

export type ToolTaskInput = {
  runId: string;
  call: ToolCallInput;
  /** Tool tasks of the same round that must settle before this call starts. */
  after: string[];
};

/**
 * The intent: the final, authorized arguments. A subagent call also records
 * its child conversation and submission once it has created them.
 */
export type ToolTaskCheckpoint = {
  arguments: JsonObject;
  subagent?: SubagentLink;
  /** A subagent call's optional label (U18), for steering by name. */
  label?: string;
  /** A typed-result subagent's one follow-up submission to its child (U19). */
  nudge?: { submissionId: string };
} | null;

/** A tool task's `done` value. */
export type ToolTaskValue = {
  entryId: string;
  status: ToolResultStatus;
  terminate: boolean;
};

export const INTERRUPTED_WARNING =
  "The tool was interrupted by a restart before it reported a result. Its effect may have happened; check before retrying.";

/**
 * The result for a call whose effect may have happened but whose outcome is
 * unknown: the standard warning plus the output committed so far.
 */
export function interruptedResult(stored: Pick<ToolOutputState, "text" | "truncated">): ToolResult {
  const partial = stored.text;
  const content =
    partial.length > 0
      ? `${INTERRUPTED_WARNING}\n\nOutput before the interruption${stored.truncated ? " (truncated)" : ""}:\n${partial}`
      : INTERRUPTED_WARNING;
  return {
    content,
    isError: true,
    details: { warning: INTERRUPTED_WARNING, partialOutput: partial, truncated: stored.truncated },
  };
}

export async function loadConversation(
  deps: Pick<AgentDeps, "session">,
  conversationId: string,
): Promise<{ conversation: ToolConversation; agent: AgentConfig }> {
  const rec = await deps.session.reader.getConversation(conversationId);
  if (!rec) throw new Error(`unknown conversation ${conversationId}`);
  const agent = await deps.session.document(agentConfigDoc, conversationId);
  const rootId = await rootConversationId(deps.session.reader, rec);
  const agentRole =
    rec.ownerTaskId === null ? undefined : await owningToolName(deps.session, rec.ownerTaskId);
  return {
    conversation: {
      id: rec.id,
      tenantId: rec.tenantId,
      projectId: rec.projectId,
      ...(rootId !== rec.id ? { rootId } : {}),
      ...(agentRole !== undefined ? { agentRole } : {}),
    },
    agent,
  };
}

/** The subagent tool name of the task that owns a child conversation (its role), if it is a tool task. */
async function owningToolName(
  session: AgentDeps["session"],
  ownerTaskId: string,
): Promise<string | undefined> {
  const task = await session.reader.getTask(ownerTaskId);
  const input = task?.input;
  if (typeof input !== "object" || input === null || Array.isArray(input)) return undefined;
  const call = input.call;
  if (typeof call !== "object" || call === null || Array.isArray(call)) return undefined;
  return typeof call.name === "string" ? call.name : undefined;
}

/**
 * Every entry of a conversation, oldest first, following the pages of the
 * storage reader to the end.
 */
export async function listAllEntries(
  session: Session,
  conversationId: string,
): Promise<EntryRecord[]> {
  const out: EntryRecord[] = [];
  for (let cursor: string | null = null; ; ) {
    const page = await session.reader.listEntries(conversationId, {
      limit: MAX_PAGE_LIMIT,
      cursor,
    });
    out.push(...page.items);
    cursor = page.nextCursor;
    if (cursor === null) return out;
  }
}

/** Append a `tool_result` entry; returns its id. */
export function appendToolResult(
  tx: Transaction,
  conversationId: string,
  taskId: string | null,
  payload: ToolResultPayload,
): string {
  return tx.appendEntry({
    conversationId,
    taskId,
    kind: ENTRY_KINDS.toolResult,
    payload: toEntryPayload(ENTRY_KINDS.toolResult, payload),
  }).id;
}

export function resultPayload(
  runId: string,
  call: Pick<ToolCallInput, "id" | "name">,
  status: ToolResultStatus,
  result: ToolResult,
  attempts: number,
): ToolResultPayload {
  return {
    runId,
    toolCallId: call.id,
    toolName: call.name,
    status,
    content: structuredClone(result.content),
    isError: result.isError ?? status !== "ok",
    details: result.details === undefined ? null : structuredClone(result.details),
    terminate: result.terminate === true,
    attempts,
  };
}

/** Gateway failure codes (`details.error`) with their own result status. */
const FAILURE_STATUSES: ReadonlySet<string> = new Set<ToolResultStatus>(["timeout", "lease_lost"]);

/** The status of a tool's own result: `ok`, a gateway failure status, or `error`. */
export function resultStatus(result: ToolResult): ToolResultStatus {
  if (!result.isError) return "ok";
  const d = result.details;
  const code =
    typeof d === "object" && d !== null && !Array.isArray(d) ? (d as JsonObject).error : undefined;
  return typeof code === "string" && FAILURE_STATUSES.has(code)
    ? (code as ToolResultStatus)
    : "error";
}

/**
 * Inside a tool task's commit: append its `tool_result` entry (carrying the
 * approval when the call needed one) and return the task's `done`.
 */
export async function writeToolResult(
  tx: Transaction,
  task: TaskState<ToolTaskInput, ToolTaskCheckpoint>,
  status: ToolResultStatus,
  result: ToolResult,
  attempts: number,
): Promise<Transition<ToolTaskCheckpoint>> {
  const payload = resultPayload(task.input.runId, task.input.call, status, result, attempts);
  if (task.phase === "approved" || Object.hasOwn(task.signals, APPROVAL_SIGNAL)) {
    const item = await approvalOf(tx, task.conversationId, task.id);
    if (item && item.status !== "pending" && item.status !== "cancelled") {
      payload.approval = resultApproval(item, tx.now);
    }
  }
  const entryId = appendToolResult(tx, task.conversationId, task.id, payload);
  const value: ToolTaskValue = { entryId, status, terminate: payload.terminate };
  return done(value);
}

/**
 * Bytes kept free for the entry record around the payload (ids, timestamps,
 * the approval summary, the truncation marker).
 */
const ENTRY_ENVELOPE_BYTES = 2048;

export const TRUNCATED_RESULT_MARKER = "[Tool result truncated to fit the entry size limit";

/** Characters per token in the engine's token estimate (see `compaction.ts`). */
const CHARS_PER_TOKEN = 4;

export const TOKEN_TRUNCATED_RESULT_MARKER = "[Tool result truncated to about";

/**
 * Cut a result to `maxTokens` (estimated as characters / 4), head kept, with a
 * note telling the model how to get the rest. A result within the cap is
 * returned unchanged.
 */
export function capToolResultTokens(result: ToolResult, maxTokens: number): ToolResult {
  const text =
    typeof result.content === "string"
      ? result.content
      : result.content.map((c) => c.text).join("\n");
  const maxChars = maxTokens * CHARS_PER_TOKEN;
  if (text.length <= maxChars) return result;
  const note =
    `\n\n${TOKEN_TRUNCATED_RESULT_MARKER} ${maxTokens} tokens: kept ${maxChars} of ${text.length} characters. ` +
    "To see more, call the tool again for a narrower slice: use its offset, limit or page " +
    "arguments if it has them, or ask for a more specific part of the data.]";
  return { ...result, content: text.slice(0, maxChars) + note };
}

/**
 * Fit a tool result into one `tool_result` entry of at most `maxEntryBytes`:
 * oversized `details` are replaced by a note and oversized content is cut
 * (head kept) with a {@link TRUNCATED_RESULT_MARKER} line. A result that
 * already fits is returned unchanged. Without this an oversized result would
 * fault the commit after the tool's effect had happened.
 */
export function capToolResult(
  call: Pick<ToolCallInput, "id" | "name">,
  result: ToolResult,
  maxEntryBytes: number,
  maxTokens?: number,
): ToolResult {
  if (maxTokens !== undefined) result = capToolResultTokens(result, maxTokens);
  const measure = (r: ToolResult) => jsonByteLength(resultPayload("", call, "ok", r, 0));
  const budget = Math.max(0, maxEntryBytes - ENTRY_ENVELOPE_BYTES);
  if (measure(result) <= budget) return result;

  let capped: ToolResult = { ...result };
  if (measure({ ...capped, content: "" }) > budget / 2) {
    capped.details = { truncated: true, note: "details dropped: too large for one entry" };
  }
  const text =
    typeof capped.content === "string"
      ? capped.content
      : capped.content.map((c) => c.text).join("\n");
  const marker = (kept: number) =>
    `\n\n${TRUNCATED_RESULT_MARKER}: kept ${kept} of ${text.length} characters.]`;
  // Shrink until it fits; JSON escaping makes bytes per character vary.
  let keep = text.length;
  for (let i = 0; i < 32 && keep > 0; i++) {
    const candidate = { ...capped, content: text.slice(0, keep) + marker(keep) };
    const size = measure(candidate);
    if (size <= budget) return candidate;
    keep = Math.max(0, Math.floor(keep * (budget / size) * 0.95) - 16);
  }
  capped = { ...capped, content: marker(0).trimStart() };
  return capped;
}

function issuesText(issues: ReadonlyArray<{ path: PropertyKey[]; message: string }>): string {
  return issues.map((i) => `${i.path.map(String).join(".") || "(root)"}: ${i.message}`).join("; ");
}

/** What a call that ran needs to offload its result. */
interface OffloadContext {
  conversation: ToolConversation;
  agent: AgentConfig;
  signal: AbortSignal;
}

type Scope = TaskScope<ToolTaskCheckpoint>;
type State = TaskState<ToolTaskInput, ToolTaskCheckpoint>;

/** The top-level conversation a call's conversation belongs to. */
const rootOf = (conversation: ToolConversation): string => conversation.rootId ?? conversation.id;

export function toolTaskDefinition(
  deps: AgentDeps,
): TaskDefinition<ToolTaskInput, ToolTaskCheckpoint> {
  /**
   * Tier 0 (R11): the result to commit. Cap it to the entry limits, then, when
   * the conversation turns offload on and a store is configured, move a big
   * one out of context. Runs before the commit, never inside it: the store
   * call is network I/O.
   */
  const settle = async (
    task: State,
    result: ToolResult,
    offload: OffloadContext | undefined,
  ): Promise<ToolResult> => {
    const capped = capToolResult(
      task.input.call,
      result,
      deps.limits.maxEntryBytes,
      deps.limits.maxToolResultTokens,
    );
    if (!deps.offload || !offload) return capped;
    const warn = (err: unknown) =>
      deps.report(err, {
        during: "offload",
        taskId: task.id,
        conversationId: task.conversationId,
      });
    let settings: ReturnType<typeof resolveCompactionSettings>;
    try {
      settings = resolveCompactionSettings(deps.compactionDefaults, offload.agent.compaction);
    } catch (err) {
      warn(err);
      return capped;
    }
    return maybeOffload({
      store: deps.offload,
      settings,
      conversation: offload.conversation,
      runId: task.input.runId,
      call: task.input.call,
      result: capped,
      signal: offload.signal,
      onWarning: warn,
    });
  };

  /** Commit the result entry and finish. */
  const finish = async (
    task: State,
    rt: Scope,
    status: ToolResultStatus,
    result: ToolResult,
    attempts: number,
    options: {
      output?: ToolOutputState | undefined;
      prepare?: ((tx: Transaction) => Promise<unknown>) | undefined;
      /** Root conversation to taint when the call ran and its result is untrusted. */
      taintRoot?: string | undefined;
      /** Set for a call that ran (or whose outcome was reconciled): its result may be offloaded. */
      offload?: OffloadContext | undefined;
    } = {},
  ): Promise<Committed> => {
    const settled = await settle(task, result, options.offload);
    return rt.commit(async (tx) => {
      if (options.prepare) await options.prepare(tx);
      if (options.taintRoot !== undefined) {
        await markTainted(tx, options.taintRoot, {
          kind: "tool_result",
          name: task.input.call.name,
          ref: task.input.call.id,
        });
      }
      if (options.output) (await tx.doc(toolOutputDoc, task.id)).set(options.output);
      return writeToolResult(tx, task, status, settled, attempts);
    });
  };

  /**
   * Ask a person (a `humanInput` tool): commit the question together with the
   * wait for its answer, to resume in `answered`.
   */
  const askQuestion = async (task: State, rt: Scope, args: JsonObject): Promise<Committed> => {
    const spec = questionSpecSchema.safeParse(args);
    if (!spec.success) {
      return fail(
        task,
        rt,
        "invalid_arguments",
        `Invalid arguments for ${task.input.call.name}: ${issuesText(spec.error.issues)}`,
      );
    }
    return rt.commit(async (tx) => {
      const expiresAt = deps.questionTtlMs === null ? null : tx.now + deps.questionTtlMs;
      await requestQuestion(tx, {
        conversationId: task.conversationId,
        toolTaskId: task.id,
        runId: task.input.runId,
        toolCallId: task.input.call.id,
        spec: spec.data,
        expiresAt,
      });
      return waiting({
        signal: ANSWER_SIGNAL,
        until: expiresAt,
        phase: "answered",
        checkpoint: { arguments: args },
      });
    });
  };

  const fail = (task: State, rt: Scope, status: ToolResultStatus, message: string) =>
    finish(task, rt, status, { content: message, isError: true }, 0);

  return defineTask<ToolTaskInput, ToolTaskCheckpoint>({
    kind: TOOL_TASK_KIND,
    phases: {
      start: async (task, rt) => {
        const pending = task.input.after.filter((id) => rt.task(id)?.status !== "terminal");
        if (pending.length > 0) return rt.commit(() => waiting({ on: pending, phase: "start" }));

        const { call, runId } = task.input;
        const { conversation, agent } = await loadConversation(deps, task.conversationId);
        const tool = deps.tools.get(call.name);
        if (!tool || !agent.tools.includes(call.name)) {
          return fail(task, rt, "unknown_tool", `Unknown tool "${call.name}".`);
        }
        if (call.invalidArguments !== null) {
          return fail(
            task,
            rt,
            "invalid_arguments",
            `Invalid arguments for ${call.name}: not a JSON object.`,
          );
        }
        const first = tool.parameters.safeParse(call.arguments);
        if (!first.success) {
          return fail(
            task,
            rt,
            "invalid_arguments",
            `Invalid arguments for ${call.name}: ${issuesText(first.error.issues)}`,
          );
        }
        const hooked = await deps.hooks.beforeTool(
          {
            conversation,
            agent,
            runId,
            toolCallId: call.id,
            toolName: call.name,
            arguments: call.arguments,
          },
          task.id,
        );
        const final = tool.parameters.safeParse(hooked);
        if (!final.success) {
          return fail(
            task,
            rt,
            "invalid_arguments",
            `Invalid arguments for ${call.name} after hooks: ${issuesText(final.error.issues)}`,
          );
        }
        // Host policy: last, on the final arguments, not overridable.
        // The arguments policy sees, the approval stores and the tool runs with are the parsed ones (schema
        // defaults applied): an approval's argument hash must cover exactly what is executed (KTD2).
        const args = final.data as JsonObject;
        const taint = taintSummary(await taintOf(deps.session, rootOf(conversation)));
        const verdict = await authorize(deps.policy, {
          conversation,
          agent,
          runId,
          toolCallId: call.id,
          toolName: call.name,
          toolTaskId: task.id,
          toolSource: tool.source,
          toolInternal: tool.internal,
          taint,
          arguments: args,
          replay: tool.replay,
        });
        switch (verdict.decision) {
          case "block":
            return fail(task, rt, "blocked", `Blocked by policy: ${verdict.reason}`);
          case "requireApproval": {
            const expiresAt = verdict.expiresAt ?? null;
            return rt.commit(async (tx) => {
              await requestApproval(tx, {
                conversationId: task.conversationId,
                toolTaskId: task.id,
                runId,
                toolCallId: call.id,
                toolName: call.name,
                arguments: args,
                reason: verdict.reason ?? null,
                expiresAt,
              });
              return waiting({
                signal: APPROVAL_SIGNAL,
                until: expiresAt,
                phase: "approved",
                checkpoint: { arguments: args },
              });
            });
          }
          case "allow": {
            if (tool.enginePhase) {
              const phase = tool.enginePhase.phase;
              return rt.commit(() => next(phase, { arguments: args }));
            }
            if (tool.humanInput) return askQuestion(task, rt, args);
            const intent = await rt.commit(() => next("execute", { arguments: args }));
            deps.freshIntents.add(task.id);
            return intent;
          }
        }
      },

      approved: async (task, rt) => {
        const decision = decisionFromSignal(task.signals[APPROVAL_SIGNAL]);
        if (decision === null) {
          if (task.wake?.reason !== "deadline") {
            // Woken without a decision or deadline: keep waiting.
            const doc = await deps.session.document(approvalsDoc, task.conversationId);
            const own = doc.items.find((i) => i.toolTaskId === task.id);
            return rt.commit(() =>
              waiting({
                signal: APPROVAL_SIGNAL,
                until: own?.expiresAt ?? null,
                phase: "approved",
              }),
            );
          }
          return finish(
            task,
            rt,
            "approval_expired",
            {
              content:
                "The approval this call required expired before a decision. The tool did not run.",
              isError: true,
            },
            0,
            { prepare: (tx) => closeApproval(tx, task.conversationId, task.id, "expired") },
          );
        }
        if (decision.decision === "deny") {
          return finish(
            task,
            rt,
            "denied",
            {
              content: `The call was denied by a human${decision.note ? `: ${decision.note}` : "."} The tool did not run.`,
              isError: true,
            },
            0,
          );
        }
        const approvedArgs = task.checkpoint?.arguments ?? {};
        const approvedTool = deps.tools.get(task.input.call.name);
        if (approvedTool?.enginePhase) {
          const phase = approvedTool.enginePhase.phase;
          return rt.commit(() => next(phase, { arguments: approvedArgs }));
        }
        if (approvedTool?.humanInput) return askQuestion(task, rt, approvedArgs);
        const intent = await rt.commit(() => next("execute", { arguments: approvedArgs }));
        deps.freshIntents.add(task.id);
        return intent;
      },

      answered: async (task, rt) => {
        const item = (await deps.session.document(questionsDoc, task.conversationId)).items.find(
          (i) => i.toolTaskId === task.id,
        );
        if (
          item?.status === "answered" &&
          item.answer !== null &&
          isAnswerSignal(task.signals[ANSWER_SIGNAL])
        ) {
          return finish(
            task,
            rt,
            "ok",
            {
              content: answerText(item),
              details: {
                questionId: item.id,
                optionIds: item.answer.optionIds,
                text: item.answer.text,
                principal: item.answer.principal,
                answeredAt: item.answer.answeredAt,
              },
            },
            0,
          );
        }
        if (task.wake?.reason !== "deadline") {
          // Woken without an answer or deadline: keep waiting.
          return rt.commit(() =>
            waiting({ signal: ANSWER_SIGNAL, until: item?.expiresAt ?? null, phase: "answered" }),
          );
        }
        return finish(
          task,
          rt,
          "question_expired",
          {
            content: "The question expired before the user answered. Continue without an answer.",
            isError: true,
          },
          0,
          { prepare: (tx) => closeQuestion(tx, task.conversationId, task.id, "expired") },
        );
      },

      execute: async (task, rt, ctx) => {
        const fresh = deps.freshIntents.delete(task.id);
        const { call } = task.input;
        const args = task.checkpoint?.arguments ?? {};
        const tool = deps.tools.get(call.name);
        if (!tool) return fail(task, rt, "error", `Tool "${call.name}" is no longer registered.`);
        const parsed = tool.parameters.safeParse(args);
        if (!parsed.success) {
          return fail(
            task,
            rt,
            "invalid_arguments",
            `Invalid arguments for ${call.name}: ${issuesText(parsed.error.issues)}`,
          );
        }
        const { conversation, agent } = await loadConversation(deps, task.conversationId);
        const stored = await deps.session.document(toolOutputDoc, task.id);
        let attempt = 1;
        if (!fresh) {
          if (tool.replay !== "safe")
            return recover(task, rt, ctx, tool, parsed.data, conversation, agent, stored);
          attempt = stored.attempt + 1;
          await rt.write(async (tx) => {
            (await tx.doc(toolOutputDoc, task.id)).set({
              text: "",
              truncated: false,
              attempt,
              toolCallId: call.id,
              toolName: call.name,
            });
          });
        }
        return run(task, rt, ctx, tool, parsed.data, args, conversation, agent, attempt);
      },

      [ENGINE_PHASE]: (task, rt) => enginePhase(task, rt),
      [SUBAGENT_PHASE]: (task, rt) => enginePhase(task, rt),
    },
    onAbort: async (task, scope) => {
      if (task.phase === "approved") {
        await scope.commit(async (tx) => {
          await closeApproval(tx, task.conversationId, task.id, "cancelled");
        });
      } else if (task.phase === "answered") {
        await scope.commit(async (tx) => {
          await closeQuestion(tx, task.conversationId, task.id, "cancelled");
        });
      }
    },
  });

  /**
   * Runs the tool's `enginePhase` handler (the seam, KTD12). A task parked in
   * the legacy `subagent` phase whose tool is no longer registered (or no
   * longer declares the seam) still follows its child to the end.
   */
  async function enginePhase(task: State, rt: Scope): Promise<Committed> {
    const handler = deps.tools.get(task.input.call.name)?.enginePhase;
    if (handler) return handler.run(deps, task, rt, writeToolResult);
    if (task.phase === SUBAGENT_PHASE) return subagentPhase(deps, task, rt, writeToolResult);
    return rt.commit((tx) =>
      writeToolResult(
        tx,
        task,
        "error",
        { content: `Tool "${task.input.call.name}" is no longer registered.`, isError: true },
        0,
      ),
    );
  }

  async function recover(
    task: State,
    rt: Scope,
    ctx: Context,
    tool: ToolDefinition,
    args: unknown,
    conversation: ToolConversation,
    agent: AgentConfig,
    stored: ToolOutputState,
  ): Promise<Committed> {
    const { call, runId } = task.input;
    const taintRoot = toolTaints(tool) ? rootOf(conversation) : undefined;
    const offload: OffloadContext = { conversation, agent, signal: ctx.signal };
    if (tool.reconcile) {
      const api: ToolReconcileApi = {
        ctx,
        idempotencyKey: `${runId}:${call.id}`,
        runId,
        toolCallId: call.id,
        taskId: task.id,
        conversation,
        partialOutput: stored.text,
      };
      try {
        const outcome = await tool.reconcile(args, api);
        if (outcome !== null && isToolResult(outcome)) {
          return finish(task, rt, resultStatus(outcome), outcome, stored.attempt, {
            taintRoot,
            offload,
          });
        }
      } catch (err) {
        ctx.throwIfCancelled();
        deps.report(err, {
          during: "reconcile",
          taskId: task.id,
          conversationId: task.conversationId,
        });
      }
    }
    ctx.throwIfCancelled();
    return finish(task, rt, "interrupted", interruptedResult(stored), stored.attempt, {
      taintRoot,
      offload,
    });
  }

  async function run(
    task: State,
    rt: Scope,
    ctx: Context,
    tool: ToolDefinition,
    parsedArgs: unknown,
    args: JsonObject,
    conversation: ToolConversation,
    agent: AgentConfig,
    attempt: number,
  ): Promise<Committed> {
    const { call, runId } = task.input;
    let output: ToolOutputState = {
      text: "",
      truncated: false,
      attempt,
      toolCallId: call.id,
      toolName: call.name,
    };
    let touched = false;
    const writer = new ProgressWriter(
      deps.clock,
      // Large snapshots are rewritten whole on every write: write them less often.
      () => toolOutputIntervalMs(deps.progressIntervalMs, output.text.length),
      async () => {
        const snapshot = output;
        await rt.write(async (tx) => {
          (await tx.doc(toolOutputDoc, task.id)).set(snapshot);
        });
      },
      (err) => {
        if (!ctx.cancelled) {
          deps.report(err, {
            during: "progress",
            taskId: task.id,
            conversationId: task.conversationId,
          });
        }
      },
    );
    const api: ToolApi = {
      ctx,
      signal: ctx.signal,
      idempotencyKey: `${runId}:${call.id}`,
      runId,
      toolCallId: call.id,
      taskId: task.id,
      attempt,
      conversation,
      output(text: string) {
        if (typeof text !== "string" || text.length === 0) return;
        output = appendOutput(output, text);
        touched = true;
        writer.mark();
      },
    };

    let result: ToolResult;
    let failed = false;
    try {
      const value = await tool.execute(parsedArgs, api);
      if (isToolResult(value)) {
        result = value;
      } else {
        result = { content: `Tool ${call.name} returned an invalid result.`, isError: true };
        failed = true;
      }
    } catch (err) {
      ctx.throwIfCancelled();
      result = { content: `Tool ${call.name} failed: ${errorText(err)}`, isError: true };
      failed = true;
    } finally {
      await writer.close();
    }
    ctx.throwIfCancelled();
    result = await deps.hooks.afterTool(
      {
        conversation,
        agent,
        runId,
        toolCallId: call.id,
        toolName: call.name,
        arguments: args,
        result,
      },
      task.id,
    );
    const status: ToolResultStatus = failed ? "error" : resultStatus(result);
    return finish(task, rt, status, result, attempt, {
      output: touched ? output : undefined,
      taintRoot: toolTaints(tool) ? rootOf(conversation) : undefined,
      offload: { conversation, agent, signal: ctx.signal },
    });
  }
}
