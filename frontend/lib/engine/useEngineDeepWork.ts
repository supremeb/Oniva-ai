'use client';

/**
 * useEngineDeepWork: one watch subscription on the root conversation, the panel
 * state derived from it (U8). A lazy second watch (`useWorkerTranscript`)
 * opens only for a worker row the user expands.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { EngineConversationClient } from './client';
import { createDeepWorkAdapter, type DeepWorkRunMeta, type DeepWorkState } from './deepWork';
import {
  cancelEngineRun,
  createWorkerWatchSource,
  listEngineWorkspace,
  steerDeepWork,
  type WorkspaceFileEntry,
} from './deepWorkApi';
import type { ConversationView } from './types';
import { useEngineConversation, type UseEngineConversation } from './useEngineConversation';
import type { WatcherOptions } from './watcher';

export interface DeepWorkApi {
  listWorkspace(sessionId: string): Promise<WorkspaceFileEntry[]>;
  steer(sessionId: string, text: string, requestId: string): Promise<unknown>;
  cancel(sessionId: string): Promise<unknown>;
}

const defaultApi: DeepWorkApi = {
  listWorkspace: listEngineWorkspace,
  steer: steerDeepWork,
  cancel: cancelEngineRun,
};

export interface UseEngineDeepWorkOptions {
  /** deep_work_runs.id: needed for workspace, steer, cancel and the worker watch. */
  sessionId?: string | null;
  meta?: DeepWorkRunMeta;
  client?: EngineConversationClient;
  watcher?: WatcherOptions;
  api?: Partial<DeepWorkApi>;
  /** Debounce for the workspace refetch after a write-tool result (default 500 ms). */
  workspaceDebounceMs?: number;
}

export interface UseEngineDeepWork {
  conversation: UseEngineConversation;
  view: ConversationView | null;
  deepWork: DeepWorkState;
  connection: UseEngineConversation['connection'];
  workspaceFiles: WorkspaceFileEntry[];
  /** Queue a steer for the running orchestrator; resolves with its request id. */
  steer(text: string): Promise<string>;
  /** Cancel the run: server-side abort, sandbox teardown and status (falls back to abort without a session id). */
  stop(): Promise<void>;
}

/** Changes whenever a tool result reports workspace writes. */
function workspaceSignature(view: ConversationView | null): string {
  if (!view) return '';
  let count = 0;
  let lastId = '';
  for (const e of view.entries) {
    if (e.kind !== 'tool_result') continue;
    const d = typeof e.payload === 'object' && e.payload !== null && !Array.isArray(e.payload) ? e.payload.details : null;
    const ws = typeof d === 'object' && d !== null && !Array.isArray(d) ? d.workspace : null;
    if (Array.isArray(ws) && ws.length > 0) {
      count++;
      lastId = e.id;
    }
  }
  return `${count}:${lastId}`;
}

export function useEngineDeepWork(
  conversationId: string | null,
  opts: UseEngineDeepWorkOptions = {},
): UseEngineDeepWork {
  const conversation = useEngineConversation(conversationId, { client: opts.client, watcher: opts.watcher });
  // Read through a ref so an inline `api` object never re-runs the effects below.
  const apiRef = useRef<Partial<DeepWorkApi> | undefined>(opts.api);
  apiRef.current = opts.api;
  const api = useMemo<DeepWorkApi>(
    () => ({
      listWorkspace: (s) => (apiRef.current?.listWorkspace ?? defaultApi.listWorkspace)(s),
      steer: (s, t, r) => (apiRef.current?.steer ?? defaultApi.steer)(s, t, r),
      cancel: (s) => (apiRef.current?.cancel ?? defaultApi.cancel)(s),
    }),
    [],
  );
  const sessionId = opts.sessionId ?? null;

  const adapter = useMemo(() => createDeepWorkAdapter(), [conversationId]); // eslint-disable-line react-hooks/exhaustive-deps
  const { status, error, planTitle } = opts.meta ?? {};
  const meta = useMemo<DeepWorkRunMeta>(() => ({ status, error, planTitle }), [status, error, planTitle]);
  const deepWork = useMemo(() => adapter(conversation.view, meta), [adapter, conversation.view, meta]);

  // -- workspace files: refetch (debounced) when a write tool reports files, and once at completion --
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFileEntry[]>([]);
  const sig = workspaceSignature(conversation.view);
  const done = deepWork.status === 'completed';
  const debounce = opts.workspaceDebounceMs ?? 500;
  useEffect(() => {
    if (!sessionId || conversation.view === null) return;
    let cancelled = false;
    const t = setTimeout(() => {
      api
        .listWorkspace(sessionId)
        .then((files) => {
          if (!cancelled) setWorkspaceFiles(files);
        })
        .catch(() => {
          /* keep the previous list; the next write retries */
        });
    }, debounce);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
    // `conversation.view` only matters for the first non-null view.
  }, [api, sessionId, sig, done, debounce, conversation.view === null]); // eslint-disable-line react-hooks/exhaustive-deps

  const steer = useCallback(
    async (text: string) => {
      if (!sessionId) throw new Error('no session');
      const requestId = crypto.randomUUID();
      await api.steer(sessionId, text, requestId);
      return requestId;
    },
    [api, sessionId],
  );

  const { abort } = conversation;
  const stop = useCallback(async () => {
    if (sessionId) await api.cancel(sessionId);
    else await abort('user_cancelled');
  }, [api, sessionId, abort]);

  return {
    conversation,
    view: conversation.view,
    deepWork,
    connection: conversation.connection,
    workspaceFiles,
    steer,
    stop,
  };
}

/**
 * Read-only transcript of one worker's child conversation. Pass null while the
 * row is collapsed: no connection exists then, and collapsing closes it.
 */
export function useWorkerTranscript(
  sessionId: string | null,
  childConversationId: string | null,
  opts: { source?: { watch: EngineConversationClient['watch'] }; watcher?: WatcherOptions } = {},
): Pick<UseEngineConversation, 'view' | 'connection' | 'error'> {
  const source = useMemo(
    () => opts.source ?? (sessionId ? createWorkerWatchSource(sessionId) : undefined),
    [opts.source, sessionId],
  );
  const { view, connection, error } = useEngineConversation(sessionId ? childConversationId : null, {
    client: source as unknown as EngineConversationClient,
    watcher: opts.watcher,
  });
  return { view, connection, error };
}
