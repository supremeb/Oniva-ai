'use client';

import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { EngineConversationClient } from './client';
import { ConversationWatcher, type PendingEntry, type WatcherOptions, type WatcherState } from './watcher';

const IDLE: WatcherState = { view: null, cursor: null, connection: 'connecting', pending: [], error: null };
const noop = () => () => {};

export interface UseEngineConversation extends WatcherState {
  /** Submit with a fresh requestId; returns it. Rejects when retries are exhausted. */
  submit(input: { text?: string; content?: unknown; whenBusy?: 'followUp' | 'steer' | 'reject' }): Promise<string>;
  /** Resend a failed pending submission with its original requestId. */
  retry(requestId: string): Promise<void>;
  abort(reason?: string): Promise<void>;
  /** Withdraw a queued input by submission id (409 once it has started). */
  cancelSubmission(submissionId: string): Promise<void>;
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
  const retry = useCallback<UseEngineConversation['retry']>(
    async (requestId) => {
      const entry = watcher?.getState().pending.find((p) => p.requestId === requestId);
      if (!watcher || !entry) return;
      const { status: _status, ...rest } = entry;
      await watcher.submit(rest);
    },
    [watcher]
  );
  const abort = useCallback(
    async (reason?: string) => {
      if (conversationId) await client.abort(conversationId, reason);
    },
    [client, conversationId]
  );
  const cancelSubmission = useCallback(
    async (submissionId: string) => {
      if (conversationId) await client.cancel(conversationId, submissionId);
    },
    [client, conversationId]
  );
  const decide = useCallback(
    async (toolTaskId: string, decision: 'approve' | 'deny', note?: string) => {
      if (conversationId) await client.decide(conversationId, toolTaskId, decision, note);
    },
    [client, conversationId]
  );

  return { ...state, submit, retry, abort, cancelSubmission, decide };
}
