/**
 * Typed workflow node gateway payloads (plan 2026-10-17-003, U33, KTD4/KTD5).
 *
 * These types are the engine's view of the committed JSON schemas under
 * `engine/contracts/workflow-nodes/`. The schemas are normative; `contract.ts`
 * validates untrusted bodies against them and this module names the shapes.
 * Every body carries `contractVersion: 1`.
 */
import type { JsonValue } from "../core/json.js";

/** The version stamped on every node gateway body. */
export const CONTRACT_VERSION = 1;

/** The version of a task checkpoint; `decodeCheckpoint` upgrades `version - 1` (KTD16). */
export const CHECKPOINT_VERSION = 1;

/** A node's own result status, matching `prepare.final`. */
export type NodeStatus = "success" | "failed" | "blocked" | "skipped";

/** The run's terminal status, matching `finalize.input`. */
export type RunStatus = "completed" | "failed" | "cancelled";

export type ResponseFormat = "text" | "json";

/** Why a `wait` node woke. */
export type WakeReason = "signal" | "deadline";

/** The prepare kinds a `finish` call reports on. */
export type FinishKind = "llm" | "wait" | "fanout";

/**
 * A node's retry, timeout and error handling. `retry` and `timeout` adornments
 * compile here; `try` compiles to `onError: "continue"` (KTD5).
 */
export interface NodePolicy {
  maxRetries: number;
  backoffMs: number;
  timeoutMs: number;
  onError: "stop" | "continue";
}

/** `prepare` finished in the backend; the node is done. */
export interface PrepareFinal {
  contractVersion: typeof CONTRACT_VERSION;
  kind: "final";
  status: NodeStatus;
  output: JsonValue;
  error?: JsonValue | undefined;
  tokensUsed: number;
  classification?: string | undefined;
  selectedHandles?: string[] | undefined;
}

/** The backend asks the engine to run a generation (KTD6). */
export interface PrepareLlm {
  contractVersion: typeof CONTRACT_VERSION;
  kind: "llm";
  system: string;
  prompt: string;
  /** A hint only; the model policy decides. Never a key. */
  tier?: string | undefined;
  /** A hint only; the model policy decides. Never a key. */
  model?: string | undefined;
  tools?: string[] | undefined;
  maxRounds?: number | undefined;
  responseFormat?: ResponseFormat | undefined;
  /** Declared by the backend: a re-run after a crash is safe. */
  replaySafe: boolean;
}

/** The node parks; duration waits are relative to the engine clock (KTD7). */
export interface PrepareWait {
  contractVersion: typeof CONTRACT_VERSION;
  kind: "wait";
  durationMs?: number | undefined;
  untilEpochMs?: number | undefined;
  signal?: string | undefined;
  slaUntilEpochMs?: number | undefined;
}

/** Where a fan-out sends its lanes. */
export type FanoutTarget = { subgraph: string } | { workflowId: string };

/** The node fans out. Counts and a backend reference only, never item payloads (KTD17). */
export interface PrepareFanout {
  contractVersion: typeof CONTRACT_VERSION;
  kind: "fanout";
  count: number;
  parallel: number;
  failFast: boolean;
  target: FanoutTarget;
  itemsRef: string;
}

/** A discriminated union on `kind`; the four `prepare` shapes. */
export type PrepareResult = PrepareFinal | PrepareLlm | PrepareWait | PrepareFanout;

/** The counts-only summary the run task delivers when a fan-out settles (KTD17). */
export interface FanoutSummary {
  count: number;
  completed: number;
  failed: number;
  cancelled: number;
  firstError?: JsonValue | undefined;
}

/** What the engine sends to the backend `finish` route. The reply is a `final`. */
export interface FinishInput {
  contractVersion: typeof CONTRACT_VERSION;
  kind: FinishKind;
  answer?: JsonValue | undefined;
  signalPayload?: JsonValue | undefined;
  wakeReason?: WakeReason | undefined;
  fanout?: FanoutSummary | undefined;
}

/** What the `workflow_run` task sends to the backend `finalize` route. */
export interface FinalizeInput {
  contractVersion: typeof CONTRACT_VERSION;
  status: RunStatus;
  nodeSummaries: Record<string, NodeStatus>;
  error?: JsonValue | undefined;
  attempts: number;
}
