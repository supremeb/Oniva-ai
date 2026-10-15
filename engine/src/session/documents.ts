/**
 * Typed JSON documents (R3, KTD5, KTD6).
 *
 * A document is identified by `(kind, scopeId)` and validated by a zod schema.
 * Inside a commit, `tx.doc(definition, scopeId)` hands out a {@link DocDraft}:
 * a private working copy that is validated and snapshotted when the commit is
 * stored, and revoked once the commit callback settles.
 */
import type { z } from "zod";
import { EngineError } from "../core/errors.js";
import { assertJson, type JsonValue } from "../core/json.js";

/** What a document is scoped to; `scopeId` is the id of that thing. */
export type DocScope = "session" | "conversation" | "task";

export interface DocDefinition<T extends JsonValue = JsonValue> {
  readonly kind: string;
  readonly scope: DocScope;
  /** Value of a document that has never been stored. */
  readonly initial: T;
  readonly schema: z.ZodType<T>;
}

/** Document data that fails its schema (or is not strict JSON). Rolls the commit back. */
export class InvalidDocument extends EngineError {
  readonly code = "invalid_json";
  constructor(
    readonly kind: string,
    readonly scopeId: string | null,
    readonly reason: string,
    options?: ErrorOptions,
  ) {
    super(`invalid ${kind} document${scopeId === null ? "" : ` ${scopeId}`}: ${reason}`, options);
  }
}

/** Validate `data` against the definition, returning the parsed (strict JSON) value. */
export function parseDoc<T extends JsonValue>(
  def: DocDefinition<T>,
  scopeId: string | null,
  data: unknown,
): T {
  const parsed = def.schema.safeParse(data);
  if (!parsed.success) {
    const reason = parsed.error.issues
      .map((i) => `${i.path.join(".") || "$"}: ${i.message}`)
      .join("; ");
    throw new InvalidDocument(def.kind, scopeId, reason, { cause: parsed.error });
  }
  try {
    assertJson(parsed.data);
  } catch (err) {
    throw new InvalidDocument(def.kind, scopeId, (err as Error).message, { cause: err });
  }
  return parsed.data;
}

export function defineDoc<T extends JsonValue>(def: {
  kind: string;
  scope: DocScope;
  initial: T;
  schema: z.ZodType<T>;
}): DocDefinition<T> {
  if (typeof def.kind !== "string" || def.kind.length === 0) {
    throw new TypeError("document kind must be a non-empty string");
  }
  const initial = parseDoc(def, null, structuredClone(def.initial));
  return Object.freeze({ kind: def.kind, scope: def.scope, initial, schema: def.schema });
}

export const docKey = (kind: string, scopeId: string): string => JSON.stringify([kind, scopeId]);

/** A draft of one document inside one commit. */
export interface DocDraft<T extends JsonValue> {
  readonly kind: string;
  readonly scopeId: string;
  /** The working copy. Mutate it in place, or replace it with `set`. */
  readonly value: T;
  set(next: T): void;
  /** Mutate the working copy, or return a replacement. */
  update(fn: (current: T) => T | undefined): void;
}

/** Thrown when a transaction or draft is used outside its commit callback. */
export class TransactionSettled extends Error {
  constructor(what: string) {
    super(`${what} used after its commit callback settled`);
    this.name = "TransactionSettled";
  }
}

/** Session-internal draft implementation. */
export class Draft<T extends JsonValue> implements DocDraft<T> {
  #value: T;
  #revoked = false;

  constructor(
    readonly def: DocDefinition<T>,
    readonly scopeId: string,
    /** Committed value, or null when the document was never stored. */
    readonly before: T | null,
  ) {
    this.#value = structuredClone(before ?? def.initial);
  }

  get kind(): string {
    return this.def.kind;
  }

  get value(): T {
    this.#check();
    return this.#value;
  }

  set(next: T): void {
    this.#check();
    this.#value = next;
  }

  update(fn: (current: T) => T | undefined): void {
    this.#check();
    const next = fn(this.#value);
    if (next !== undefined) this.#value = next;
  }

  revoke(): void {
    this.#revoked = true;
  }

  /** The working copy, for the session to snapshot after revocation. */
  draftValue(): T {
    return this.#value;
  }

  #check(): void {
    if (this.#revoked) throw new TransactionSettled(`draft of ${this.def.kind} ${this.scopeId}`);
  }
}
