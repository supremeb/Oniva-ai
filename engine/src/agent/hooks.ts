/**
 * Generic host hooks (R9).
 *
 * Hooks run in registration order. `beforeRequest` may replace the model
 * input, `beforeTool` may rewrite the arguments, `afterTool` may replace the
 * result; `afterResponse` only observes. A hook that throws is reported and
 * skipped (its change, if any, is dropped) and the rest still run.
 *
 * Hooks never decide whether a tool may run: `HostPolicy.authorizeTool`
 * runs after every `beforeTool` hook, on the final arguments, and its verdict
 * is final.
 */
import type { JsonObject } from "../core/json.js";
import type { AssistantMessage, ModelInput, Usage } from "../models/types.js";
import type { ToolConversation, ToolResult } from "../tools/registry.js";
import type { AgentConfig } from "./prompt.js";

export interface BeforeRequestEvent {
  conversation: ToolConversation;
  agent: AgentConfig;
  runId: string;
  round: number;
  input: ModelInput;
}

export interface AfterResponseEvent {
  conversation: ToolConversation;
  agent: AgentConfig;
  runId: string;
  round: number;
  message: AssistantMessage;
  usage: Usage;
}

/** A compaction summary request finished (a reply came back, usable or not). */
export interface AfterCompactionEvent {
  conversation: ToolConversation;
  agent: AgentConfig;
  /** The run and round whose request started the compaction. */
  runId: string;
  round: number;
  /** The compaction task: stable across retries and crash re-runs. */
  taskId: string;
  message: AssistantMessage;
  usage: Usage;
  /** Structured summaries only: how the summary compares with what it replaced. */
  quality?: CompactionQuality;
}

/** Size and shape of a structured summary (`structuredSummary`). */
export interface CompactionQuality {
  summaryChars: number;
  summarizedChars: number;
  /** `summaryChars / summarizedChars`; null when nothing text-bearing was summarized. */
  ratio: number | null;
  /** Headings the model left out (restored from the previous summary or marked empty). */
  missingHeadings: string[];
}

export interface BeforeToolEvent {
  conversation: ToolConversation;
  agent: AgentConfig;
  runId: string;
  toolCallId: string;
  toolName: string;
  arguments: JsonObject;
}

export interface AfterToolEvent extends BeforeToolEvent {
  result: ToolResult;
}

type MaybePromise<T> = T | Promise<T>;
/** A hook's optional change; returning nothing keeps the value as it is. */
type Change<T> = MaybePromise<T | undefined>;

export interface AgentHooks {
  beforeRequest?(event: BeforeRequestEvent): Change<{ input?: ModelInput }>;
  afterResponse?(event: AfterResponseEvent): MaybePromise<void>;
  /** Observes the model spend of a compaction summary request. */
  afterCompaction?(event: AfterCompactionEvent): MaybePromise<void>;
  beforeTool?(event: BeforeToolEvent): Change<{ arguments?: JsonObject }>;
  afterTool?(event: AfterToolEvent): Change<{ result?: ToolResult }>;
}

export type HookName = keyof AgentHooks;

/** Where a reported (non-fatal) error came from. */
export interface AgentErrorInfo {
  during:
    | `hook:${HookName}`
    | "progress"
    | "reconcile"
    | "offload"
    | "compaction_quality"
    | "settle"
    | "handler"
    | "abort"
    | "scheduler";
  taskId?: string;
  conversationId?: string;
}

export type AgentErrorReporter = (error: unknown, info: AgentErrorInfo) => void;

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Runs a list of hooks with the error policy above. */
export class HookRunner {
  readonly #hooks: readonly AgentHooks[];
  readonly #report: AgentErrorReporter;

  constructor(hooks: AgentHooks | readonly AgentHooks[] | undefined, report: AgentErrorReporter) {
    this.#hooks =
      hooks === undefined ? [] : Array.isArray(hooks) ? [...hooks] : [hooks as AgentHooks];
    this.#report = report;
  }

  async beforeRequest(event: BeforeRequestEvent, taskId: string): Promise<ModelInput> {
    let input = event.input;
    for (const h of this.#hooks) {
      if (!h.beforeRequest) continue;
      try {
        const out = await h.beforeRequest({ ...event, input: structuredClone(input) });
        if (isObject(out) && isObject(out.input) && Array.isArray(out.input.messages)) {
          input = structuredClone(out.input);
        }
      } catch (err) {
        this.#report(err, { during: "hook:beforeRequest", taskId });
      }
    }
    return input;
  }

  async afterResponse(event: AfterResponseEvent, taskId: string): Promise<void> {
    for (const h of this.#hooks) {
      if (!h.afterResponse) continue;
      try {
        await h.afterResponse(structuredClone(event));
      } catch (err) {
        this.#report(err, { during: "hook:afterResponse", taskId });
      }
    }
  }

  async afterCompaction(event: AfterCompactionEvent, taskId: string): Promise<void> {
    for (const h of this.#hooks) {
      if (!h.afterCompaction) continue;
      try {
        await h.afterCompaction(structuredClone(event));
      } catch (err) {
        this.#report(err, { during: "hook:afterCompaction", taskId });
      }
    }
  }

  async beforeTool(event: BeforeToolEvent, taskId: string): Promise<JsonObject> {
    let args = event.arguments;
    for (const h of this.#hooks) {
      if (!h.beforeTool) continue;
      try {
        const out = await h.beforeTool({ ...event, arguments: structuredClone(args) });
        if (isObject(out) && isObject(out.arguments))
          args = structuredClone(out.arguments) as JsonObject;
      } catch (err) {
        this.#report(err, { during: "hook:beforeTool", taskId });
      }
    }
    return args;
  }

  async afterTool(event: AfterToolEvent, taskId: string): Promise<ToolResult> {
    let result = event.result;
    for (const h of this.#hooks) {
      if (!h.afterTool) continue;
      try {
        const out = await h.afterTool({ ...event, result: structuredClone(result) });
        if (isObject(out) && isObject(out.result) && isToolResult(out.result)) {
          result = structuredClone(out.result);
        }
      } catch (err) {
        this.#report(err, { during: "hook:afterTool", taskId });
      }
    }
    return result;
  }
}

export function isToolResult(value: unknown): value is ToolResult {
  if (!isObject(value)) return false;
  const c = value.content;
  return (
    typeof c === "string" ||
    (Array.isArray(c) &&
      c.every((p) => isObject(p) && p.type === "text" && typeof p.text === "string"))
  );
}
