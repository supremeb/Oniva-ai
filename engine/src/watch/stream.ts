/**
 * The committed-state watch stream (R17, KTD5).
 *
 * A {@link WatchHub} follows a session's commits and keeps, for each watched
 * conversation, its current view and a retained window of recent frames (in
 * memory, `retainFrames` per conversation). Frames are published only after
 * a commit is adopted, and only for commits that change the conversation's
 * view:
 *
 * - `{ cursor, patch }`: the commit's seq and the JSON Patch from the
 *   previous view;
 * - `{ cursor, snapshot }`: the whole view as of `cursor`.
 *
 * Cursors are the session's commit seqs, so they are monotonic. A watcher
 * starts with a snapshot, or resumes from a cursor: inside the retained
 * window it gets just the frames after the cursor; outside it (or from a
 * cursor this process never produced) it gets a snapshot.
 *
 * Commits never wait for watchers. Each consumer buffers its frames; one more
 * than `maxLag` frames behind drops them and gets a single fresh snapshot
 * when it next reads.
 */

import type { CommitEvent } from "../session/publish.js";
import type { Session } from "../session/session.js";
import type { JsonPatchOp } from "./patch.js";
import { applyCommitToView, type ConversationView, loadViewState, type ViewState } from "./view.js";

export interface PatchFrame {
  cursor: number;
  patch: JsonPatchOp[];
}

export interface SnapshotFrame {
  cursor: number;
  snapshot: ConversationView;
}

export type WatchFrame = PatchFrame | SnapshotFrame;

export const isSnapshotFrame = (frame: WatchFrame): frame is SnapshotFrame => "snapshot" in frame;

export interface WatchOptions {
  /** Frames retained per conversation for resuming. Default 500. */
  retainFrames?: number;
  /** A consumer more than this many frames behind gets a snapshot instead. Default 100. */
  maxLag?: number;
  /** Unwatched conversations whose view and window are kept. Default 64. */
  maxIdleConversations?: number;
}

export const DEFAULT_WATCH_OPTIONS: Readonly<Required<WatchOptions>> = Object.freeze({
  retainFrames: 500,
  maxLag: 100,
  maxIdleConversations: 64,
});

/** What a hub watches: anything holding a session (a `Harness`, for one). */
export interface WatchSource {
  readonly session: Session;
}

export interface WatchStart {
  /** The last cursor the client saw, or null/undefined to start from a snapshot. */
  cursor?: number | null;
}

/** The hub was closed (or its session ended). */
export class WatchClosed extends Error {
  constructor() {
    super("watch hub is closed");
    this.name = "WatchClosed";
  }
}

class Channel {
  /** Seq the view is current as of. */
  asOf: number;
  /** Frames with a cursor above `floor` are all retained. */
  floor: number;
  readonly frames: PatchFrame[] = [];
  readonly consumers = new Set<WatchConsumer>();

  constructor(
    readonly conversationId: string,
    readonly state: ViewState,
    seq: number,
  ) {
    this.asOf = seq;
    this.floor = seq;
  }

  snapshot(): SnapshotFrame {
    return { cursor: this.asOf, snapshot: structuredClone(this.state.view) };
  }
}

/** One watcher's frames. Read with `next()` (waits) or `poll()` (does not). */
export class WatchConsumer {
  readonly conversationId: string;
  readonly #channel: Channel;
  readonly #maxLag: number;
  readonly #onClose: (consumer: WatchConsumer) => void;
  #queue: WatchFrame[] = [];
  #needSnapshot = false;
  #closed = false;
  #wake: (() => void) | null = null;

  /** @internal */
  constructor(channel: Channel, maxLag: number, onClose: (consumer: WatchConsumer) => void) {
    this.conversationId = channel.conversationId;
    this.#channel = channel;
    this.#maxLag = maxLag;
    this.#onClose = onClose;
  }

  /** Frames waiting to be read (a pending snapshot counts as one). */
  get pending(): number {
    return this.#queue.length + (this.#needSnapshot ? 1 : 0);
  }

  get closed(): boolean {
    return this.#closed;
  }

  /** The next frame if one is ready, else null. */
  poll(): WatchFrame | null {
    if (this.#closed) return null;
    if (this.#needSnapshot) {
      this.#needSnapshot = false;
      this.#queue = [];
      return this.#channel.snapshot();
    }
    return this.#queue.shift() ?? null;
  }

  /** The next frame, waiting for one; null once the consumer is closed. */
  async next(): Promise<WatchFrame | null> {
    for (;;) {
      const frame = this.poll();
      if (frame) return frame;
      if (this.#closed) return null;
      await new Promise<void>((resolve) => {
        this.#wake = resolve;
      });
    }
  }

  /** Stop watching. Idempotent; a pending `next()` resolves null. */
  close(): void {
    if (this.#closed) return;
    this.#closed = true;
    this.#queue = [];
    this.#needSnapshot = false;
    this.#notify();
    this.#onClose(this);
  }

  async *[Symbol.asyncIterator](): AsyncGenerator<WatchFrame> {
    for (;;) {
      const frame = await this.next();
      if (frame === null) return;
      yield frame;
    }
  }

  /** @internal */
  startWith(frames: PatchFrame[] | "snapshot"): void {
    if (frames === "snapshot") this.#needSnapshot = true;
    else this.#queue.push(...frames);
  }

  /** @internal Never blocks: buffers, or collapses into a snapshot when too far behind. */
  push(frame: PatchFrame): void {
    if (this.#closed || this.#needSnapshot) return;
    this.#queue.push(frame);
    if (this.#queue.length > this.#maxLag) {
      this.#queue = [];
      this.#needSnapshot = true;
    }
    this.#notify();
  }

  #notify(): void {
    const wake = this.#wake;
    this.#wake = null;
    wake?.();
  }
}

function checkCount(name: string, value: number): number {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new RangeError(`${name} must be a positive integer, got ${value}`);
  }
  return value;
}

export class WatchHub {
  readonly session: Session;
  readonly options: Readonly<Required<WatchOptions>>;
  readonly #channels = new Map<string, Channel>();
  readonly #loading = new Map<string, Promise<Channel>>();
  /** Unwatched channels, oldest first. */
  readonly #idle = new Set<string>();
  readonly #unsubscribe: () => void;
  #closed = false;

  constructor(source: WatchSource, options: WatchOptions = {}) {
    const merged = { ...DEFAULT_WATCH_OPTIONS, ...options };
    this.options = Object.freeze({
      retainFrames: checkCount("retainFrames", merged.retainFrames),
      maxLag: checkCount("maxLag", merged.maxLag),
      maxIdleConversations: checkCount("maxIdleConversations", merged.maxIdleConversations),
    });
    this.session = source.session;
    this.#unsubscribe = this.session.subscribeCommits((event) => this.#onCommit(event));
    void this.session.closed.then(() => this.close());
  }

  get closed(): boolean {
    return this.#closed;
  }

  /**
   * Start watching a conversation. Rejects with `UnknownConversation` for an
   * id the session does not hold, and `WatchClosed` once the hub is closed.
   */
  async watch(conversationId: string, start: WatchStart = {}): Promise<WatchConsumer> {
    let channel = await this.#channel(conversationId);
    // A channel evicted while we waited no longer follows commits: load anew.
    while (this.#channels.get(conversationId) !== channel) {
      if (this.#closed) throw new WatchClosed();
      channel = await this.#channel(conversationId);
    }
    const consumer = new WatchConsumer(channel, this.options.maxLag, (c) => this.#release(c));
    const cursor = start.cursor ?? null;
    if (cursor === null || cursor < channel.floor || cursor > channel.asOf) {
      consumer.startWith("snapshot");
    } else {
      const missed = channel.frames.filter((f) => f.cursor > cursor);
      consumer.startWith(missed.length > this.options.maxLag ? "snapshot" : missed);
    }
    channel.consumers.add(consumer);
    this.#idle.delete(conversationId);
    return consumer;
  }

  /** The hub's current view of a conversation and the cursor it is current as of. */
  async current(conversationId: string): Promise<{ cursor: number; view: ConversationView }> {
    const channel = await this.#channel(conversationId);
    return { cursor: channel.asOf, view: structuredClone(channel.state.view) };
  }

  /** Stop following commits and close every consumer. Idempotent. */
  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    this.#unsubscribe();
    for (const channel of this.#channels.values()) {
      for (const consumer of [...channel.consumers]) consumer.close();
    }
    this.#channels.clear();
    this.#idle.clear();
  }

  // ---- internals ------------------------------------------------------------

  async #channel(conversationId: string): Promise<Channel> {
    if (this.#closed) throw new WatchClosed();
    const existing = this.#channels.get(conversationId);
    if (existing) return existing;
    let loading = this.#loading.get(conversationId);
    if (!loading) {
      // Load on the commit line: no commit can land between reading the view
      // and registering it, and every earlier commit has been published.
      loading = this.session
        .commit(async () => {
          const state = await loadViewState(this.session, conversationId);
          const channel = new Channel(conversationId, state, this.session.lastSeq);
          if (!this.#closed) {
            this.#channels.set(conversationId, channel);
            this.#idle.add(conversationId);
            this.#evictIdle();
          }
          return channel;
        })
        .finally(() => this.#loading.delete(conversationId));
      this.#loading.set(conversationId, loading);
    }
    return loading;
  }

  #onCommit(event: CommitEvent): void {
    for (const channel of this.#channels.values()) {
      const patch = applyCommitToView(channel.state, channel.conversationId, event);
      channel.asOf = event.seq;
      if (patch.length === 0) continue;
      const frame: PatchFrame = { cursor: event.seq, patch };
      channel.frames.push(frame);
      while (channel.frames.length > this.options.retainFrames) {
        channel.floor = (channel.frames.shift() as PatchFrame).cursor;
      }
      for (const consumer of channel.consumers) consumer.push(frame);
    }
  }

  #release(consumer: WatchConsumer): void {
    const channel = this.#channels.get(consumer.conversationId);
    if (!channel) return;
    channel.consumers.delete(consumer);
    if (channel.consumers.size === 0) {
      this.#idle.add(consumer.conversationId);
      this.#evictIdle();
    }
  }

  #evictIdle(): void {
    while (this.#idle.size > this.options.maxIdleConversations) {
      const oldest = this.#idle.values().next().value as string;
      this.#idle.delete(oldest);
      this.#channels.delete(oldest);
    }
  }
}
