/**
 * Host policy (R2, R9, KTD8).
 *
 * One `HostPolicy` object is given at harness open. It has the last word:
 * - `resolveModelRequest` runs before every model request and returns the
 *   provider, model, key and token budget, or a denial. Keys are used for the
 *   one request and never stored.
 * - `authorizeTool` runs on every tool call after the generic `beforeTool`
 *   hooks, on the final arguments. Its verdict is final: nothing in the
 *   conversation configuration can remove or override it, and a throw blocks
 *   the call.
 */
import type { Context } from "../core/context.js";
import type { JsonObject } from "../core/json.js";
import type { ModelResolution } from "../models/types.js";
import type { ReplayPolicy, ToolConversation, ToolSource } from "../tools/registry.js";
import type { AgentConfig } from "./prompt.js";
import type { TaintState } from "./taint.js";

/**
 * Why a model request is being made: `generation` answers the conversation,
 * `compaction` summarizes older context (hosts typically route it to a
 * cheaper model, e.g. `DEFAULT_ANTHROPIC_SUMMARY_MODEL`).
 */
export type ModelPurpose = "generation" | "compaction";

export interface ModelRequestInfo {
  /** Cancelled with the requesting task. */
  ctx: Context;
  purpose: ModelPurpose;
  conversation: ToolConversation;
  agent: AgentConfig;
  runId: string;
  /**
   * 0 for a run's first request, then one more per tool round. For a
   * compaction: the run and round that started it.
   */
  round: number;
}

export interface ToolAuthorizationRequest {
  conversation: ToolConversation;
  agent: AgentConfig;
  runId: string;
  toolCallId: string;
  toolName: string;
  /** The tool task handling the call; the id an approval decision names. */
  toolTaskId: string;
  /** Where the tool comes from, so policy can treat gateway tools differently. */
  toolSource: ToolSource;
  /** The tool is flagged `internal` (ask_user, subagent spawn): side-effect free, never a taint source. */
  toolInternal?: boolean;
  /** The conversation's taint state, so policy can react to untrusted input. */
  taint: TaintState;
  /** The final arguments, after every `beforeTool` hook. */
  arguments: JsonObject;
  replay: ReplayPolicy;
}

export type ToolVerdict =
  | { decision: "allow" }
  | { decision: "block"; reason: string }
  | {
      decision: "requireApproval";
      reason?: string;
      /** Injected-clock epoch ms after which the request expires. */
      expiresAt?: number;
    };

export interface HostPolicy {
  resolveModelRequest(info: ModelRequestInfo): ModelResolution | Promise<ModelResolution>;
  authorizeTool(request: ToolAuthorizationRequest): ToolVerdict | Promise<ToolVerdict>;
  /**
   * Context window of a model this policy has resolved before (from
   * `ModelResolution.contextWindow`), or undefined when unknown. Compaction
   * thresholds use it ahead of the engine's default window.
   */
  contextWindowFor?(model: string): number | undefined;
}

/** Run `authorizeTool`. A throw or a malformed verdict blocks the call. */
export async function authorize(
  policy: HostPolicy,
  request: ToolAuthorizationRequest,
): Promise<ToolVerdict> {
  let verdict: ToolVerdict;
  try {
    verdict = await policy.authorizeTool(structuredClone(request));
  } catch (err) {
    const why = err instanceof Error ? err.message : String(err);
    return { decision: "block", reason: `tool authorization failed: ${why}` };
  }
  switch (verdict?.decision) {
    case "allow":
      return { decision: "allow" };
    case "block":
      return {
        decision: "block",
        reason: typeof verdict.reason === "string" ? verdict.reason : "blocked by policy",
      };
    case "requireApproval": {
      const out: ToolVerdict = { decision: "requireApproval" };
      if (typeof verdict.reason === "string") out.reason = verdict.reason;
      if (typeof verdict.expiresAt === "number" && Number.isFinite(verdict.expiresAt)) {
        out.expiresAt = verdict.expiresAt;
      }
      return out;
    }
    default:
      return { decision: "block", reason: "tool authorization returned no valid verdict" };
  }
}
