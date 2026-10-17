/**
 * Tools (R6, KTD6).
 *
 * A tool is a name, a description, zod parameters (sent to providers as JSON
 * Schema) and an `execute` function. Its flags shape how the agent loop runs
 * it:
 *
 * - `replay`: `"safe"` means executing it again after a crash is harmless
 *   (reads, idempotent writes), so a tool task interrupted between intent and
 *   result simply reruns it with the same idempotency key. Anything else is
 *   `"unsafe"` (the default): after a crash the tool's `reconcile` is asked
 *   for the stored outcome, and without one the model gets an `interrupted`
 *   result warning that the effect may have happened.
 * - `sequential`: the call does not overlap any other call of its round;
 *   calls before it finish first and calls after it wait for it.
 */
import type { z } from "zod";
import { toJSONSchema } from "zod";
import type { Context } from "../core/context.js";
import type { JsonObject, JsonValue } from "../core/json.js";
import type { TextContent, ToolSpec } from "../models/types.js";

export type ReplayPolicy = "safe" | "unsafe";

/**
 * Where a tool comes from: `"gateway"` for Oniva's backend tools loaded from
 * the manifest (`tools/gateway.ts`), `"native"` (the default) for tools defined
 * in the engine or by a host. It decides whether a call's result taints the
 * conversation (see `agent/taint.ts`): a gateway tool taints only when the
 * manifest marks it `untrustedOutput`, while a native tool taints unless it is
 * flagged `internal` or is one of the internal engine tools.
 */
export type ToolSource = "gateway" | "native";

/**
 * Engine-native tools the model may call that are not output-bearing and so
 * never taint a conversation. Mirrored by the backend (`tool_policy.py`); the
 * shared fixture `test/support/internal-native-tools.json` keeps them in step.
 */
export const INTERNAL_NATIVE_TOOLS: readonly string[] = Object.freeze([
  "ask_user",
  "complete_task",
  "present_plan",
  "update_todo",
]);

/** What a tool returns. Strings and text parts reach the model as-is. */
export interface ToolResult {
  content: string | TextContent[];
  /** Report the call as failed to the model. */
  isError?: boolean;
  /** Structured data for hosts and UIs; not sent to the model. */
  details?: JsonValue;
  /** When every result of a round says so, the run ends after the round. */
  terminate?: boolean;
}

/** The conversation a call runs for (from the stored binding, never from model output). */
export interface ToolConversation {
  id: string;
  tenantId: string;
  projectId: string;
  /**
   * The top-level conversation this one belongs to when it is a subagent's
   * child conversation (found by walking the owner chain). Absent for a
   * top-level conversation.
   */
  rootId?: string;
  /**
   * For a subagent's child conversation, the name of the subagent tool that
   * created it (e.g. `research_worker`); absent for a top-level conversation.
   * Reported to the usage ledger so spend can be split by role.
   */
  agentRole?: string;
}

/**
 * The ids a backend request carries: `conversationId` is the top-level
 * conversation (the only one the backend knows), `engineConversationId` the
 * conversation that actually made the call.
 */
export function backendConversationIds(conversation: ToolConversation): {
  conversationId: string;
  engineConversationId: string;
} {
  return {
    conversationId: conversation.rootId ?? conversation.id,
    engineConversationId: conversation.id,
  };
}

/** What `execute` gets besides its arguments. */
export interface ToolApi {
  /** Cancelled when the call is aborted or the worker stops (lease loss, shutdown). */
  readonly ctx: Context;
  readonly signal: AbortSignal;
  /** `runId:toolCallId`; the same on every attempt of this call. */
  readonly idempotencyKey: string;
  readonly runId: string;
  readonly toolCallId: string;
  /** The tool task's id. */
  readonly taskId: string;
  /** 1 on the first execution; higher when a replay-safe call reruns after a restart. */
  readonly attempt: number;
  readonly conversation: ToolConversation;
  /** Append partial output; it is committed (throttled) so a restart can report it. */
  output(text: string): void;
}

/** What `reconcile` gets: the same identity as the interrupted execution. */
export interface ToolReconcileApi {
  readonly ctx: Context;
  readonly idempotencyKey: string;
  readonly runId: string;
  readonly toolCallId: string;
  readonly taskId: string;
  readonly conversation: ToolConversation;
  /** Output the interrupted execution committed before the restart. */
  readonly partialOutput: string;
}

export interface ToolDefinition<A = unknown> {
  readonly name: string;
  readonly description: string;
  readonly parameters: z.ZodType<A>;
  readonly replay: ReplayPolicy;
  readonly sequential: boolean;
  /** Where the tool comes from; `"native"` by default. */
  readonly source: ToolSource;
  /** An engine-internal tool (never taints, e.g. the subagent spawn tool). */
  readonly internal: boolean;
  /** Whether the tool's result is untrusted input for the conversation. */
  readonly untrustedOutput: boolean;
  /**
   * The tool asks a person instead of executing: its arguments must match
   * `questionSpecSchema` (`agent/questions.ts`), the tool task parks on a
   * durable question and the answer becomes the result. `execute` is not called.
   */
  readonly humanInput: boolean;
  execute(args: A, api: ToolApi): ToolResult | Promise<ToolResult>;
  /**
   * After a restart that interrupted an `unsafe` call between intent and
   * result: the call's stored outcome (for example from a gateway, by
   * idempotency key), or null when there is none. A throw counts as none.
   */
  reconcile?(args: A, api: ToolReconcileApi): ToolResult | null | Promise<ToolResult | null>;
}

export interface ToolDefinitionInput<A> {
  name: string;
  description: string;
  parameters: z.ZodType<A>;
  replay?: ReplayPolicy;
  sequential?: boolean;
  /** Where the tool comes from; `"native"` by default. */
  source?: ToolSource;
  /** Mark an engine-internal tool so its result never taints the conversation. */
  internal?: boolean;
  /** Mark the tool's result as untrusted input for the conversation. */
  untrustedOutput?: boolean;
  /** Mark a tool that asks a person; see {@link ToolDefinition.humanInput}. */
  humanInput?: boolean;
  execute(args: A, api: ToolApi): ToolResult | Promise<ToolResult>;
  reconcile?(args: A, api: ToolReconcileApi): ToolResult | null | Promise<ToolResult | null>;
}

const TOOL_NAME = /^[A-Za-z0-9_-]{1,64}$/;

export function defineTool<A>(input: ToolDefinitionInput<A>): ToolDefinition<A> {
  if (!TOOL_NAME.test(input.name)) {
    throw new TypeError(`tool name must match ${TOOL_NAME}, got ${JSON.stringify(input.name)}`);
  }
  if (input.replay !== undefined && input.replay !== "safe" && input.replay !== "unsafe") {
    throw new TypeError(`tool ${input.name}: replay must be "safe" or "unsafe"`);
  }
  if (input.source !== undefined && input.source !== "gateway" && input.source !== "native") {
    throw new TypeError(`tool ${input.name}: source must be "gateway" or "native"`);
  }
  if (typeof input.execute !== "function") {
    throw new TypeError(`tool ${input.name}: execute must be a function`);
  }
  return Object.freeze({
    name: input.name,
    description: input.description,
    parameters: input.parameters,
    replay: input.replay ?? "unsafe",
    sequential: input.sequential ?? false,
    source: input.source ?? "native",
    internal: input.internal ?? false,
    untrustedOutput: input.untrustedOutput ?? false,
    humanInput: input.humanInput ?? false,
    execute: input.execute,
    ...(input.reconcile ? { reconcile: input.reconcile } : {}),
  });
}

/** JSON Schema for a tool's parameters, as sent to providers. */
export function parametersSchema(tool: ToolDefinition): JsonObject {
  const schema = toJSONSchema(tool.parameters, { unrepresentable: "any" }) as Record<
    string,
    unknown
  >;
  const { $schema: _drop, ...rest } = schema;
  return JSON.parse(JSON.stringify(rest)) as JsonObject;
}

/** Tool definitions by name. */
export class ToolRegistry {
  // biome-ignore lint/suspicious/noExplicitAny: erased argument type
  readonly #tools = new Map<string, ToolDefinition<any>>();
  readonly #specs = new Map<string, ToolSpec>();

  // biome-ignore lint/suspicious/noExplicitAny: erased argument type
  constructor(tools: Iterable<ToolDefinition<any>> = []) {
    for (const tool of tools) this.register(tool);
  }

  /** Add a tool. The same object twice is a no-op; another tool with a taken name throws. */
  register<A>(tool: ToolDefinition<A>): this {
    const existing = this.#tools.get(tool.name);
    if (existing && existing !== tool)
      throw new TypeError(`tool ${tool.name} is already registered`);
    this.#tools.set(tool.name, tool);
    this.#specs.delete(tool.name);
    return this;
  }

  get(name: string): ToolDefinition | undefined {
    return this.#tools.get(name);
  }

  names(): string[] {
    return [...this.#tools.keys()];
  }

  /** Provider specs for the given names that are registered, in the given order. */
  specs(names: readonly string[]): ToolSpec[] {
    const out: ToolSpec[] = [];
    for (const name of names) {
      const tool = this.#tools.get(name);
      if (!tool) continue;
      let spec = this.#specs.get(name);
      if (!spec) {
        spec = { name, description: tool.description, parameters: parametersSchema(tool) };
        this.#specs.set(name, spec);
      }
      out.push(structuredClone(spec));
    }
    return out;
  }
}
