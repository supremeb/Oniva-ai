'use client';

import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { EngineConversationClient } from './client';
import { ConversationWatcher, type PendingEntry, type WatcherOptions, type WatcherState } from './watcher';

const IDLE: WatcherState = { view: null, cursor: null, connection: 'connecting', pending: [], error: null };
const noop = () => () => {};

export interface UseEngineConversation extends WatcherState {
  /** Submit with a fresh requestId; returns it. Rejects when retries are exhausted. */
  submit(input: { text?: string; content?: unknown; whenBusy?: 'followUp' | 'steer' | 'reject' }): Promise<string>;
  abort(reason?: string): Promise<void>;
  decide(toolTaskId: string, decision: 'approve' | 'deny', note?: string): Promise<void>;
}

export function useEngineConversation(
  conversationId: string | null,
  opts: { client?: EngineConversationClient; watcher?: WatcherOptions } = {}
): UseEngineConversation {
  const client = useMemo(() => opts.client ?? new EngineConversationClient(), [opts.client]);
  const watcherOpts = useRef(opts.watcher);
  const watcher = useMemo(
    () => (conversationId ? new ConversationWatcher(conversationId, client, watcherOpts.current) : null),
    [conversationId, client]
  );

  useEffect(() => {
    if (!watcher) return;
    watcher.start();
    return () => watcher.close();
  }, [watcher]);

  const state = useSyncExternalStore(
    watcher ? watcher.subscribe : noop,
    watcher ? watcher.getState : () => IDLE,
    () => IDLE
  );

  const submit = useCallback<UseEngineConversation['submit']>(
    async (input) => {
      if (!watcher) throw new Error('no conversation');
      const requestId = crypto.randomUUID();
      const entry: Omit<PendingEntry, 'status'> = { requestId, ...input };
      await watcher.submit(entry);
      return requestId;
    },
    [watcher]
  );
  const abort = useCallback(
    async (reason?: string) => {
      if (conversationId) await client.abort(conversationId, reason);
    },
    [client, conversationId]
  );
  const decide = useCallback(
    async (toolTaskId: string, decision: 'approve' | 'deny', note?: string) => {
      if (conversationId) await client.decide(conversationId, toolTaskId, decision, note);
    },
    [client, conversationId]
  );

  return { ...state, submit, abort, decide };
}
