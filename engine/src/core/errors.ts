/**
 * Typed engine errors. Every error carries a stable `code` so callers (and the
 * HTTP layer) can branch without string-matching messages.
 */

export type EngineErrorCode =
  | "session_failed"
  | "storage_request"
  | "lease_lost"
  | "conversation_busy"
  | "not_owner"
  | "limit_exceeded"
  | "cancelled"
  | "deadline_exceeded"
  | "invalid_json";

export abstract class EngineError extends Error {
  abstract readonly code: EngineErrorCode;

  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = new.target.name;
  }
}

export function isEngineError(value: unknown): value is EngineError {
  return value instanceof EngineError;
}

/** The session can no longer make progress (corrupt state, fatal storage error, ...). */
export class SessionFailed extends EngineError {
  readonly code = "session_failed";
  constructor(
    readonly sessionId: string,
    message: string,
    options?: ErrorOptions,
  ) {
    super(`session ${sessionId} failed: ${message}`, options);
  }
}

/** A storage backend request failed. `retryable` says whether retrying may help. */
export class StorageRequestError extends EngineError {
  readonly code = "storage_request";
  readonly retryable: boolean;
  constructor(message: string, options: { retryable: boolean; cause?: unknown }) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    this.retryable = options.retryable;
  }
}

/** This worker no longer holds the session lease (expired, or taken over with a newer token). */
export class LeaseLost extends EngineError {
  readonly code = "lease_lost";
  constructor(
    readonly sessionId: string,
    readonly token: number,
    options?: ErrorOptions,
  ) {
    super(`lease lost for session ${sessionId} (token ${token})`, options);
  }
}

/** The conversation already has an active generation. */
export class ConversationBusy extends EngineError {
  readonly code = "conversation_busy";
  constructor(readonly conversationId: string) {
    super(`conversation ${conversationId} is busy`);
  }
}

/** The request reached a worker that does not own the session. */
export class NotOwner extends EngineError {
  readonly code = "not_owner";
  constructor(
    readonly sessionId: string,
    readonly ownerId: string | undefined,
  ) {
    super(`session ${sessionId} is owned by ${ownerId ?? "nobody"}`);
  }
}

/** An engine limit (see limits.ts) was exceeded. */
export class LimitExceeded extends EngineError {
  readonly code = "limit_exceeded";
  constructor(
    readonly limit: string,
    readonly actual: number,
    readonly max: number,
  ) {
    super(`${limit} exceeded: ${actual} > ${max}`);
  }
}

/** Default cancellation reason for a context cancelled without an explicit reason. */
export class Cancelled extends EngineError {
  readonly code = "cancelled";
  constructor(message = "cancelled") {
    super(message);
  }
}

/** Cancellation reason for a context whose deadline passed. */
export class DeadlineExceeded extends EngineError {
  readonly code = "deadline_exceeded";
  constructor(readonly deadline: number) {
    super(`deadline exceeded at ${deadline}`);
  }
}

/** A value is not strict JSON. `path` points at the offending location (`$.a[2].b`). */
export class InvalidJson extends EngineError {
  readonly code = "invalid_json";
  constructor(
    readonly path: string,
    readonly reason: string,
    options?: ErrorOptions,
  ) {
    super(`invalid JSON at ${path}: ${reason}`, options);
  }
}
