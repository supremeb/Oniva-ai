/**
 * The watch state machine, independent of React: connecting -> live ->
 * reconnecting -> live, with resume by cursor, snapshot on gap, backoff and
 * stalled detection (KTD4, KTD9, KTD15).
 */
import { type EngineConversationClient, type SubmitInput, WatchHttpError } from './client';
import { applyJsonPatch } from './patch';
import { readSse } from './sse';
import {
  type ConversationView,
  isSnapshotFrame,
  type JsonValue,
  parseWatchFrame,
  type WatchFrame,
} from './types';

export type Connection = 'connecting' | 'live' | 'reconnecting' | 'stalled' | 'closed';

export interface PendingEntry {
  requestId: string;
  text?: string;
  content?: unknown;
  whenBusy?: 'followUp' | 'steer' | 'reject';
  status: 'sending' | 'failed';
}

export interface WatcherState {
  view: ConversationView | null;
  cursor: number | null;
  connection: Connection;
  pending: PendingEntry[];
  /** Terminal error (404 / flag off, or a failed non-retryable stream). */
  error: string | null;
}

export interface WatcherOptions {
  stalledAfterMs?: number;
  backoffBaseMs?: number;
  backoffCapMs?: number;
  random?: () => number;
}

const hasRequestId = (view: ConversationView, id: string): boolean =>
  view.entries.some(
    (e) =>
      typeof e.payload === 'object' &&
      e.payload !== null &&
      !Array.isArray(e.payload) &&
      e.payload.requestId === id
  );

export class ConversationWatcher {
  private state: WatcherState = {
    view: null,
    cursor: null,
    connection: 'connecting',
    pending: [],
    error: null,
  };
  private listeners = new Set<() => void>();
  private abort: AbortController | null = null;
  private closed = false;
  private started = false;
  /** Bumped per start so a loop from before a close/restart stops instead of doubling up. */
  private generation = 0;
  private stallTimer: ReturnType<typeof setTimeout> | null = null;
  private wake: (() => void) | null = null;
  private readonly stalledAfterMs: number;
  private readonly base: number;
  private readonly cap: number;
  private readonly random: () => number;

  constructor(
    private readonly conversationId: string,
    private readonly client: EngineConversationClient,
    opts: WatcherOptions = {}
  ) {
    this.stalledAfterMs = opts.stalledAfterMs ?? 45_000;
    this.base = opts.backoffBaseMs ?? 500;
    this.cap = opts.backoffCapMs ?? 15_000;
    this.random = opts.random ?? Math.random;
  }

  getState = (): WatcherState => this.state;

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  /** Begin watching. Deferred a microtask so a strict-mode mount/unmount opens nothing. */
  start(): void {
    if (this.started && !this.closed) return;
    // Restart after close (strict-mode mount/unmount/mount): reconnect from the last cursor.
    if (this.closed) {
      this.closed = false;
      this.set({ connection: 'connecting' });
    }
    this.started = true;
    const gen = ++this.generation;
    queueMicrotask(() => {
      if (this.live(gen)) void this.loop(gen);
    });
  }

  private live(gen: number): boolean {
    return !this.closed && gen === this.generation;
  }

  close(): void {
    this.closed = true;
    this.clearStall();
    this.abort?.abort();
    this.wake?.();
    this.set({ connection: 'closed' });
  }

  /** Optimistic submit keyed by `requestId`; retried by the client with the same id. */
  async submit(entry: Omit<PendingEntry, 'status'>): Promise<void> {
    const { requestId, ...rest } = entry;
    if (!this.state.pending.some((p) => p.requestId === requestId)) {
      this.set({ pending: [...this.state.pending, { requestId, ...rest, status: 'sending' }] });
    } else {
      // A retry of a failed submit reuses the same requestId (idempotent on the server).
      this.set({
        pending: this.state.pending.map((p) => (p.requestId === requestId ? { ...p, status: 'sending' } : p)),
      });
    }
    try {
      const input: SubmitInput = { requestId, ...rest };
      await this.client.submit(this.conversationId, input);
    } catch (e) {
      this.set({
        pending: this.state.pending.map((p) => (p.requestId === requestId ? { ...p, status: 'failed' } : p)),
      });
      throw e;
    }
  }

  private set(patch: Partial<WatcherState>): void {
    this.state = { ...this.state, ...patch };
    for (const l of this.listeners) l();
  }

  private isRunActive(): boolean {
    return this.state.view?.live.runId != null;
  }

  private clearStall(): void {
    if (this.stallTimer) clearTimeout(this.stallTimer);
    this.stallTimer = null;
  }

  /** Reset the silence timer on any frame or heartbeat. */
  private touch(): void {
    this.clearStall();
    if (this.state.connection === 'stalled') this.set({ connection: 'live' });
    this.stallTimer = setTimeout(() => {
      if (!this.closed && this.state.connection === 'live' && this.isRunActive()) {
        this.set({ connection: 'stalled' });
      }
    }, this.stalledAfterMs);
  }

  private async sleep(ms: number): Promise<void> {
    await new Promise<void>((resolve) => {
      const t = setTimeout(resolve, ms);
      this.wake = () => {
        clearTimeout(t);
        resolve();
      };
    });
    this.wake = null;
  }

  private onFrame(frame: WatchFrame): 'ok' | 'resync' {
    const s = this.state;
    if (isSnapshotFrame(frame)) {
      this.commit(frame.snapshot, frame.cursor);
      return 'ok';
    }
    if (s.view === null) return 'resync'; // patch without a base
    if (s.cursor !== null && frame.cursor <= s.cursor) return 'ok'; // duplicate/old
    try {
      this.commit(applyJsonPatch(s.view as unknown as JsonValue, frame.patch) as unknown as ConversationView, frame.cursor);
      return 'ok';
    } catch {
      return 'resync';
    }
  }

  private commit(view: ConversationView, cursor: number): void {
    const stillPending = this.state.pending.filter((p) => !hasRequestId(view, p.requestId));
    this.set({ view, cursor, pending: stillPending, connection: 'live', error: null });
  }

  private async loop(gen: number): Promise<void> {
    let failures = 0;
    while (this.live(gen)) {
      const ac = new AbortController();
      this.abort = ac;
      let resync = false;
      let retryAfter: number | null = null;
      try {
        const res = await this.client.watch(this.conversationId, this.state.cursor, ac.signal);
        if (res.body === null) throw new Error('no body');
        this.touch();
        await readSse(
          res.body,
          (ev) => {
            if (!this.live(gen) || resync) return;
            if (ev.event !== 'snapshot' && ev.event !== 'patch') return;
            let frame: WatchFrame;
            try {
              frame = parseWatchFrame(JSON.parse(ev.data));
            } catch {
              resync = true;
              ac.abort();
              return;
            }
            failures = 0;
            if (this.onFrame(frame) === 'resync') {
              resync = true;
              ac.abort();
            }
          },
          () => this.touch()
        );
      } catch (e) {
        if (!this.live(gen)) return;
        if (e instanceof WatchHttpError) {
          if (e.status === 404 || e.status === 403 || e.status === 401) {
            this.clearStall();
            this.set({ connection: 'closed', error: e.status === 404 ? 'not_found' : 'forbidden' });
            return;
          }
          retryAfter = e.retryAfterMs;
        }
      }
      if (!this.live(gen)) return;
      this.clearStall();
      if (resync) {
        // Reconnect without a cursor to receive a snapshot.
        this.state = { ...this.state, cursor: null };
        failures = 0;
        this.set({ connection: 'reconnecting' });
        continue;
      }
      failures++;
      this.set({ connection: 'reconnecting' });
      const exp = Math.min(this.cap, this.base * 2 ** (failures - 1));
      const delay = Math.max(retryAfter ?? 0, exp * (0.75 + this.random() * 0.5));
      await this.sleep(delay);
    }
  }
}
