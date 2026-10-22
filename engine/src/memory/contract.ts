/**
 * The wire contract of the memory curator gateway (plan 2026-10-21-001, U30,
 * KTD18). The engine's `memory_curate` host task is a thin driver: every
 * semantic step (the digest, the curator prompt, delta parsing, the taint rule,
 * budgets) lives in the backend. Two keyed `POST` routes carry it:
 *
 * - `/internal/memory/curate/prepare` ({@link CuratePrepareRequest} →
 *   {@link CuratePrepareResponse}), idempotency key `curate:{conversationId}:{runId}:prepare`;
 * - `/internal/memory/curate/apply` ({@link CurateApplyRequest} →
 *   {@link CurateApplyResponse}), idempotency key `curate:{conversationId}:{runId}:apply`.
 *
 * `conversationId` and `runId` identify the SOURCE run being curated (the
 * chat or Deep Work run), not the curator's own run. The executable
 * specification is `test/support/fake-memory-backend.ts`.
 *
 * Validators here are strict about shape and bounds and ignore unknown fields
 * of a response (a newer backend may add some); a request the engine builds is
 * always validated before it is sent.
 */

export const MEMORY_CONTRACT_VERSION = 1;

/** Checkpoint version of the `memory_curate` task. */
export const MEMORY_CHECKPOINT_VERSION = 1;

export const MEMORY_LIMITS = {
  /** Longest id (`conversationId`, `runId`, task and conversation ids). */
  idChars: 256,
  /** Longest curator system prompt. */
  systemChars: 32_000,
  /** Longest curator user prompt (the digest). */
  promptChars: 200_000,
  /** Longest opaque `ctx` token the backend hands out and takes back. */
  ctxChars: 4_096,
  /** Longest child answer the engine will post to `apply`. */
  answerChars: 131_072,
  /** Longest skip reason. */
  reasonChars: 500,
  /** Most counters in an apply response. */
  counters: 32,
} as const;

export const MEMORY_TIERS = ["high", "mid", "low"] as const;
export type MemoryTier = (typeof MEMORY_TIERS)[number];

export type MemoryContractErrorCode = "invalid_request" | "invalid_response";

/** A payload broke the contract; `code` is stable. */
export class MemoryContractError extends Error {
  constructor(
    readonly code: MemoryContractErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "MemoryContractError";
  }
}

/** Body of `POST /internal/memory/curate/prepare`. */
export interface CuratePrepareRequest {
  contractVersion: typeof MEMORY_CONTRACT_VERSION;
  /** The source conversation being curated (the backend's conversation id). */
  conversationId: string;
  /** The source run being curated. */
  runId: string;
  /** The curator run's own engine conversation, so the backend can authorize the binding. */
  curateConversationId: string;
  /** The curator task's id. */
  curateTaskId: string;
}

/** Nothing to curate (mode off, budget spent, already curated at this sequence, ...). */
export interface CuratePrepareSkip {
  contractVersion: typeof MEMORY_CONTRACT_VERSION;
  kind: "skip";
  reason?: string;
}

/** Ask the model for deltas: `system` and `prompt` go to a tool-less child at `tier`. */
export interface CuratePrepareLlm {
  contractVersion: typeof MEMORY_CONTRACT_VERSION;
  kind: "llm";
  system: string;
  prompt: string;
  tier: MemoryTier;
  /** Opaque to the engine; echoed to `apply` unchanged. */
  ctx: string;
}

export type CuratePrepareResponse = CuratePrepareSkip | CuratePrepareLlm;

/** Body of `POST /internal/memory/curate/apply`. */
export interface CurateApplyRequest {
  contractVersion: typeof MEMORY_CONTRACT_VERSION;
  conversationId: string;
  runId: string;
  /** The `ctx` of the prepare response. */
  ctx: string;
  /** The child's answer text, verbatim; the backend parses and validates it. */
  answer: string;
}

/** `200` answer of `apply`: how the backend settled the deltas. Counters are informational. */
export interface CurateApplyResponse {
  contractVersion: typeof MEMORY_CONTRACT_VERSION;
  ok: true;
  /** `applied`, or `skipped` when the backend declined at apply time (e.g. the budget ran out). */
  status: "applied" | "skipped";
  /** Free counters, e.g. `{ applied, pending, dropped, ignored }`. */
  counts?: Record<string, number>;
  reason?: string;
}

function fail(code: MemoryContractErrorCode, message: string): never {
  throw new MemoryContractError(code, message);
}

function record(
  raw: unknown,
  code: MemoryContractErrorCode,
  what: string,
): Record<string, unknown> {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return fail(code, `${what} must be an object`);
  }
  return raw as Record<string, unknown>;
}

function text(
  o: Record<string, unknown>,
  key: string,
  code: MemoryContractErrorCode,
  max: number,
  allowEmpty = false,
): string {
  const v = o[key];
  if (typeof v !== "string") return fail(code, `${key} must be a string`);
  if (!allowEmpty && v.length === 0) return fail(code, `${key} must not be empty`);
  if (v.length > max) return fail(code, `${key} is longer than ${max} characters`);
  return v;
}

function version(o: Record<string, unknown>, code: MemoryContractErrorCode): void {
  if (o.contractVersion !== MEMORY_CONTRACT_VERSION) {
    fail(code, `contractVersion must be ${MEMORY_CONTRACT_VERSION}`);
  }
}

/** Validate a prepare request. Throws {@link MemoryContractError} (`invalid_request`). */
export function parseCuratePrepareRequest(raw: unknown): CuratePrepareRequest {
  const o = record(raw, "invalid_request", "prepare request");
  version(o, "invalid_request");
  const id = (key: string) => text(o, key, "invalid_request", MEMORY_LIMITS.idChars);
  return {
    contractVersion: MEMORY_CONTRACT_VERSION,
    conversationId: id("conversationId"),
    runId: id("runId"),
    curateConversationId: id("curateConversationId"),
    curateTaskId: id("curateTaskId"),
  };
}

/** Validate a prepare response. Throws {@link MemoryContractError} (`invalid_response`). */
export function parseCuratePrepareResponse(raw: unknown): CuratePrepareResponse {
  const o = record(raw, "invalid_response", "prepare response");
  version(o, "invalid_response");
  if (o.kind === "skip") {
    const reason =
      o.reason === undefined || o.reason === null
        ? undefined
        : text(o, "reason", "invalid_response", MEMORY_LIMITS.reasonChars, true);
    return {
      contractVersion: MEMORY_CONTRACT_VERSION,
      kind: "skip",
      ...(reason === undefined ? {} : { reason }),
    };
  }
  if (o.kind === "llm") {
    const tier = o.tier;
    if (typeof tier !== "string" || !(MEMORY_TIERS as readonly string[]).includes(tier)) {
      return fail("invalid_response", `tier must be one of ${MEMORY_TIERS.join(", ")}`);
    }
    return {
      contractVersion: MEMORY_CONTRACT_VERSION,
      kind: "llm",
      system: text(o, "system", "invalid_response", MEMORY_LIMITS.systemChars),
      prompt: text(o, "prompt", "invalid_response", MEMORY_LIMITS.promptChars),
      tier: tier as MemoryTier,
      ctx: text(o, "ctx", "invalid_response", MEMORY_LIMITS.ctxChars, true),
    };
  }
  return fail("invalid_response", 'kind must be "skip" or "llm"');
}

/** Validate an apply request. Throws {@link MemoryContractError} (`invalid_request`). */
export function parseCurateApplyRequest(raw: unknown): CurateApplyRequest {
  const o = record(raw, "invalid_request", "apply request");
  version(o, "invalid_request");
  return {
    contractVersion: MEMORY_CONTRACT_VERSION,
    conversationId: text(o, "conversationId", "invalid_request", MEMORY_LIMITS.idChars),
    runId: text(o, "runId", "invalid_request", MEMORY_LIMITS.idChars),
    ctx: text(o, "ctx", "invalid_request", MEMORY_LIMITS.ctxChars, true),
    answer: text(o, "answer", "invalid_request", MEMORY_LIMITS.answerChars),
  };
}

/** Validate an apply response. Throws {@link MemoryContractError} (`invalid_response`). */
export function parseCurateApplyResponse(raw: unknown): CurateApplyResponse {
  const o = record(raw, "invalid_response", "apply response");
  version(o, "invalid_response");
  if (o.ok !== true) return fail("invalid_response", "ok must be true");
  if (o.status !== "applied" && o.status !== "skipped") {
    return fail("invalid_response", 'status must be "applied" or "skipped"');
  }
  let counts: Record<string, number> | undefined;
  if (o.counts !== undefined && o.counts !== null) {
    const c = record(o.counts, "invalid_response", "counts");
    const entries = Object.entries(c);
    if (entries.length > MEMORY_LIMITS.counters) {
      return fail("invalid_response", `counts has more than ${MEMORY_LIMITS.counters} entries`);
    }
    counts = {};
    for (const [k, v] of entries) {
      if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 0) {
        return fail("invalid_response", `counts.${k} must be a non-negative integer`);
      }
      counts[k] = v;
    }
  }
  const reason =
    o.reason === undefined || o.reason === null
      ? undefined
      : text(o, "reason", "invalid_response", MEMORY_LIMITS.reasonChars, true);
  return {
    contractVersion: MEMORY_CONTRACT_VERSION,
    ok: true,
    status: o.status,
    ...(counts === undefined ? {} : { counts }),
    ...(reason === undefined ? {} : { reason }),
  };
}
