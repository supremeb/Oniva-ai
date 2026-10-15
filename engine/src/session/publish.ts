/**
 * Commit publication. Observers hear about a commit only after storage has
 * accepted it and the session has adopted it, so they never see uncommitted
 * state. Each event carries the stored batch, its per-session `seq` and the
 * before/after of every document it touched (the watch stream diffs those).
 */
import type { JsonValue } from "../core/json.js";
import type { CommitBatch } from "../storage/types.js";

export interface DocumentChange {
  kind: string;
  scopeId: string;
  /** Committed value before this commit, or null when it was never stored. */
  before: JsonValue | null;
  after: JsonValue;
}

export interface CommitEvent {
  sessionId: string;
  seq: number;
  /** The batch as stored (redacted, with documents snapshotted). */
  batch: CommitBatch;
  documents: DocumentChange[];
}

/**
 * Called synchronously after each adopted commit, in commit order. Observers
 * must not block: buffer and return. A throwing observer is reported to the
 * session's `onObserverError` and otherwise ignored.
 */
export type CommitObserver = (event: CommitEvent) => void;

export class Publisher {
  readonly #observers = new Set<CommitObserver>();

  constructor(readonly onError: (error: unknown) => void) {}

  subscribe(observer: CommitObserver): () => void {
    // Wrap so the same function can be subscribed twice and removed independently.
    const entry: CommitObserver = (e) => observer(e);
    this.#observers.add(entry);
    return () => {
      this.#observers.delete(entry);
    };
  }

  publish(event: CommitEvent): void {
    for (const observer of [...this.#observers]) {
      try {
        // Each observer gets its own copy, so none can alter what others see.
        observer(structuredClone(event));
      } catch (err) {
        this.onError(err);
      }
    }
  }

  clear(): void {
    this.#observers.clear();
  }
}
