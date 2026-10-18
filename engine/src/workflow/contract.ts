/**
 * Workflow node gateway validators (plan 2026-10-17-003, U33, KTD4/KTD5/KTD16).
 *
 * The committed JSON schemas under `contracts/workflow-nodes/` are normative,
 * and this module is the engine's proof that it agrees with them: the zod
 * schemas here are checked against those schemas by `test/workflow/contract.test.ts`.
 *
 * `parsePrepareResponse`, `parseFinishInput` and `parseFinalizeInput` accept a
 * JSON text or an already-parsed value, reject a body over {@link MAX_BODY_BYTES}
 * (KTD17: a body can never become an unbounded checkpoint) and throw
 * {@link ContractError} with a stable `code` (`invalid_body` or `body_too_large`).
 * The backend's `app/internal_api/workflow_contract.py` rejects with the same codes.
 */
import { z } from "zod";
import type { JsonValue } from "../core/json.js";
import {
  CHECKPOINT_VERSION,
  CONTRACT_VERSION,
  type FinalizeInput,
  type FinishInput,
  type NodePolicy,
  type PrepareResult,
} from "./types.js";

export { CHECKPOINT_VERSION, CONTRACT_VERSION } from "./types.js";

/** Longest contract body accepted, in bytes. */
export const MAX_BODY_BYTES = 64 * 1024;

/** Bound mirrored from `contracts/workflow-nodes/limits.json`. */
export const MAX_PARALLEL = 50;

/** Bound mirrored from `contracts/workflow-nodes/limits.json`. */
export const MAX_FANOUT_ITEMS = 1000;

export type ContractErrorCode = "invalid_body" | "body_too_large";

/** A contract body was rejected; `code` is stable across the engine and the backend. */
export class ContractError extends Error {
  constructor(
    readonly code: ContractErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "ContractError";
  }
}

const version = z.literal(CONTRACT_VERSION);

export const prepareFinalSchema = z.strictObject({
  contractVersion: version,
  kind: z.literal("final"),
  status: z.enum(["success", "failed", "blocked", "skipped"]),
  output: z.json(),
  error: z.json().optional(),
  tokensUsed: z.number().int().min(0),
  classification: z.string().optional(),
  selectedHandles: z.array(z.string()).optional(),
});

export const prepareLlmSchema = z.strictObject({
  contractVersion: version,
  kind: z.literal("llm"),
  system: z.string(),
  prompt: z.string(),
  tier: z.string().optional(),
  model: z.string().optional(),
  tools: z.array(z.string()).optional(),
  maxRounds: z.number().int().positive().optional(),
  responseFormat: z.enum(["text", "json"]).optional(),
  replaySafe: z.boolean(),
});

export const prepareWaitSchema = z
  .strictObject({
    contractVersion: version,
    kind: z.literal("wait"),
    durationMs: z.number().int().positive().optional(),
    untilEpochMs: z.number().int().positive().optional(),
    signal: z.string().optional(),
    slaUntilEpochMs: z.number().int().positive().optional(),
  })
  .refine((value) => (value.durationMs === undefined) !== (value.untilEpochMs === undefined), {
    message: "exactly one of durationMs or untilEpochMs is required",
    path: ["durationMs"],
  });

const fanoutTargetSchema = z.union([
  z.strictObject({ subgraph: z.string() }),
  z.strictObject({ workflowId: z.string() }),
]);

export const prepareFanoutSchema = z.strictObject({
  contractVersion: version,
  kind: z.literal("fanout"),
  count: z.number().int().min(0).max(MAX_FANOUT_ITEMS),
  parallel: z.number().int().min(1).max(MAX_PARALLEL),
  failFast: z.boolean(),
  target: fanoutTargetSchema,
  itemsRef: z.string(),
});

/** The four prepare shapes as one discriminated union on `kind`. */
export const prepareResponseSchema: z.ZodType<PrepareResult> = z.discriminatedUnion("kind", [
  prepareFinalSchema,
  prepareLlmSchema,
  prepareWaitSchema,
  prepareFanoutSchema,
]);

const fanoutSummarySchema = z.strictObject({
  count: z.number().int().min(0),
  completed: z.number().int().min(0),
  failed: z.number().int().min(0),
  cancelled: z.number().int().min(0),
  firstError: z.json().optional(),
});

export const finishInputSchema: z.ZodType<FinishInput> = z.strictObject({
  contractVersion: version,
  kind: z.enum(["llm", "wait", "fanout"]),
  answer: z.json().optional(),
  signalPayload: z.json().optional(),
  wakeReason: z.enum(["signal", "deadline"]).optional(),
  fanout: fanoutSummarySchema.optional(),
});

export const finalizeInputSchema: z.ZodType<FinalizeInput> = z.strictObject({
  contractVersion: version,
  status: z.enum(["completed", "failed", "cancelled"]),
  nodeSummaries: z.record(z.string(), z.enum(["success", "failed", "blocked", "skipped"])),
  error: z.json().optional(),
  attempts: z.number().int().min(1),
});

export const nodePolicySchema: z.ZodType<NodePolicy> = z.strictObject({
  maxRetries: z.number().int().min(0),
  backoffMs: z.number().int().min(0),
  timeoutMs: z.number().int().min(0),
  onError: z.enum(["stop", "continue"]),
});

function sizeOf(raw: unknown): number {
  const text = typeof raw === "string" ? raw : JSON.stringify(raw);
  return text === undefined ? 0 : Buffer.byteLength(text, "utf8");
}

function parseBody<T>(schema: z.ZodType<T>, raw: unknown): T {
  if (sizeOf(raw) > MAX_BODY_BYTES) {
    throw new ContractError("body_too_large", `contract body exceeds ${MAX_BODY_BYTES} bytes`);
  }
  let value = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw) as unknown;
    } catch (err) {
      throw new ContractError("invalid_body", "contract body is not valid JSON", { cause: err });
    }
  }
  const parsed = schema.safeParse(value);
  if (!parsed.success) throw new ContractError("invalid_body", z.prettifyError(parsed.error));
  return parsed.data;
}

/** Validate a `prepare` response body (JSON text or parsed value). Throws {@link ContractError}. */
export function parsePrepareResponse(raw: unknown): PrepareResult {
  return parseBody(prepareResponseSchema, raw);
}

/** Validate a `finish` input body (JSON text or parsed value). Throws {@link ContractError}. */
export function parseFinishInput(raw: unknown): FinishInput {
  return parseBody(finishInputSchema, raw);
}

/** Validate a `finalize` input body (JSON text or parsed value). Throws {@link ContractError}. */
export function parseFinalizeInput(raw: unknown): FinalizeInput {
  return parseBody(finalizeInputSchema, raw);
}

export type CheckpointErrorCode = "invalid_checkpoint" | "unsupported_checkpoint_version";

/** A stored task checkpoint could not be read or is from a newer worker (KTD16). */
export class CheckpointError extends Error {
  constructor(
    readonly code: CheckpointErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "CheckpointError";
  }
}

export interface DecodeCheckpointOptions {
  /** The version this worker writes. Default {@link CHECKPOINT_VERSION}. */
  current?: number;
  /** Upgrade a checkpoint of `current - 1` (or an unversioned one) in memory. */
  upgrade?: (previous: JsonValue) => JsonValue;
}

/**
 * Read a task checkpoint. A checkpoint with no `version` is treated as version
 * 0 and upgraded; one from a newer worker throws
 * `unsupported_checkpoint_version` (KTD16).
 */
export function decodeCheckpoint<C = JsonValue>(
  raw: unknown,
  options: DecodeCheckpointOptions = {},
): C {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new CheckpointError("invalid_checkpoint", "checkpoint must be an object");
  }
  const current = options.current ?? CHECKPOINT_VERSION;
  const record = raw as Record<string, JsonValue>;
  const stored = "version" in record ? record.version : 0;
  if (typeof stored !== "number" || !Number.isInteger(stored) || stored < 0) {
    throw new CheckpointError(
      "invalid_checkpoint",
      "checkpoint version must be a non-negative integer",
    );
  }
  if (stored > current) {
    throw new CheckpointError(
      "unsupported_checkpoint_version",
      `checkpoint version ${stored} is newer than this worker supports (${current})`,
    );
  }
  if (stored === current) return record as C;
  const upgraded = options.upgrade ? options.upgrade(record) : record;
  if (upgraded === null || typeof upgraded !== "object" || Array.isArray(upgraded)) {
    throw new CheckpointError("invalid_checkpoint", "checkpoint upgrade must return an object");
  }
  return { ...(upgraded as Record<string, JsonValue>), version: current } as C;
}
