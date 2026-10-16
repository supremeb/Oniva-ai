/**
 * Engine Deep Work API (backend U7), all under /api/v1/deep-work. The legacy
 * client in lib/api/deep-work.ts stays as it is; these add project_id,
 * conversation ids and the worker watch.
 */
import { API_BASE_URL, apiFetch, attemptTokenRefresh } from '@/lib/api';
import type { DeepWorkFullState } from '@/lib/stores/deep-work';
import type { IntakeQuestion } from '@/lib/types/deep-work';
import { WatchHttpError } from './client';

const BASE = '/api/v1/deep-work';

export interface ClarifyEngineInput {
  query: string;
  /** Required on the first call of a session. */
  projectId?: string;
  availableKbs?: string[];
  availableConnectors?: string[];
  sessionId?: string;
  /** The chat the run starts from, so a refreshed chat finds it. */
  chatId?: string;
}

export interface ClarifyEngineResult {
  action: 'ask' | 'proceed';
  questions?: IntakeQuestion[] | null;
  enriched_query?: string | null;
  session_id: string;
  conversation_id: string;
}

export interface PlanEngineInput {
  query: string;
  enrichedQuery?: string;
  sessionId?: string;
  conversationId?: string;
  projectId?: string;
  chatId?: string;
}

export interface PlanEngineResult {
  title: string;
  steps: string[];
  raw_plan: Record<string, unknown>;
  session_id: string;
  conversation_id: string;
}

export interface StartEngineRunInput {
  sessionId: string;
  query: string;
  plan?: Record<string, unknown>;
  enrichedQuery?: string;
  knowledgeBaseIds?: string[];
  connectorIds?: string[];
  chatId?: string;
}

export interface StartEngineRunResult {
  session_id: string;
  conversation_id: string;
  status: 'started';
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Only a server conversation id links a run to its chat; a brand-new chat's local draft key is not one yet. */
const chatIdField = (chatId?: string) => (chatId && UUID_RE.test(chatId) ? { chat_id: chatId } : {});

export const clarifyEngine = (i: ClarifyEngineInput): Promise<ClarifyEngineResult> =>
  apiFetch(`${BASE}/engine/clarify`, {
    method: 'POST',
    timeout: 30000,
    body: JSON.stringify({
      query: i.query,
      ...(i.projectId ? { project_id: i.projectId } : {}),
      available_kbs: i.availableKbs ?? [],
      available_connectors: i.availableConnectors ?? [],
      ...(i.sessionId ? { session_id: i.sessionId } : {}),
      ...chatIdField(i.chatId),
    }),
  });

/** 422 detail `{message, text, error}` after the server's one repair: surfaced as the thrown APIError. */
export const planEngine = (i: PlanEngineInput): Promise<PlanEngineResult> =>
  apiFetch(`${BASE}/engine/plan`, {
    method: 'POST',
    timeout: 120000,
    body: JSON.stringify({
      query: i.query,
      ...(i.enrichedQuery ? { enriched_query: i.enrichedQuery } : {}),
      ...(i.sessionId ? { session_id: i.sessionId } : {}),
      ...(i.conversationId ? { conversation_id: i.conversationId } : {}),
      ...(i.projectId ? { project_id: i.projectId } : {}),
      ...chatIdField(i.chatId),
    }),
  });

/** 409 while a run is active. */
export const startEngineRun = (i: StartEngineRunInput): Promise<StartEngineRunResult> =>
  apiFetch(`${BASE}/engine`, {
    method: 'POST',
    timeout: 30000,
    body: JSON.stringify({
      session_id: i.sessionId,
      query: i.query,
      ...(i.plan ? { plan: i.plan } : {}),
      ...(i.enrichedQuery ? { enriched_query: i.enrichedQuery } : {}),
      ...(i.knowledgeBaseIds ? { knowledge_base_ids: i.knowledgeBaseIds } : {}),
      ...(i.connectorIds ? { connector_ids: i.connectorIds } : {}),
      ...chatIdField(i.chatId),
    }),
  });

export const cancelEngineRun = (sessionId: string): Promise<{ session_id: string; status: string }> =>
  apiFetch(`${BASE}/engine/${encodeURIComponent(sessionId)}/cancel`, { method: 'POST', timeout: 15000 });

/** Same `requestId` as Idempotency-Key, so a retry never queues the steer twice. */
export const steerDeepWork = (sessionId: string, text: string, requestId: string): Promise<unknown> =>
  apiFetch(`${BASE}/${encodeURIComponent(sessionId)}/steer`, {
    method: 'POST',
    headers: { 'Idempotency-Key': requestId },
    timeout: 15000,
    body: JSON.stringify({ text, requestId }),
  });

export const getEngineFullState = async (conversationId: string): Promise<DeepWorkFullState | null> => {
  try {
    return await apiFetch<DeepWorkFullState>(`${BASE}/by-conversation/${encodeURIComponent(conversationId)}/full-state`, {
      timeout: 10000,
    });
  } catch (e) {
    if (e instanceof Error && e.message.includes('404')) return null;
    throw e;
  }
};

export interface WorkspaceFileEntry {
  name: string;
  path: string;
  size: number;
  type: string;
  modified_at: number;
}

export const listEngineWorkspace = async (sessionId: string): Promise<WorkspaceFileEntry[]> => {
  const res = await apiFetch<{ files?: WorkspaceFileEntry[] } | WorkspaceFileEntry[]>(
    `${BASE}/${encodeURIComponent(sessionId)}/workspace`,
    { timeout: 10000 },
  );
  return Array.isArray(res) ? res : (res.files ?? []);
};

/** The part of EngineConversationClient the watcher uses to read. */
export interface WatchSource {
  watch(conversationId: string, cursor: number | null, signal: AbortSignal): Promise<Response>;
}

function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

/**
 * Watch client for a worker's child conversation. The backend checks the child
 * id against the root's subagents; auth is the session cookie (the query-token
 * fallback exists for EventSource, which this does not use).
 */
export function createWorkerWatchSource(sessionId: string): WatchSource {
  return {
    async watch(childConversationId, cursor, signal) {
      const qs = cursor !== null ? `?cursor=${cursor}` : '';
      const url = `${API_BASE_URL}${BASE}/${encodeURIComponent(sessionId)}/workers/${encodeURIComponent(childConversationId)}/watch${qs}`;
      for (let attempt = 0; ; attempt++) {
        const headers: Record<string, string> = { Accept: 'text/event-stream' };
        const csrf = getCookie('csrf_token');
        if (csrf) headers['X-CSRF-Token'] = csrf;
        const res = await fetch(url, { headers, credentials: 'include', signal, cache: 'no-store' });
        if (res.status === 401 && attempt === 0 && (await attemptTokenRefresh())) continue;
        if (!res.ok || !res.body) {
          const ra = Number(res.headers.get('Retry-After'));
          throw new WatchHttpError(res.status, Number.isFinite(ra) && ra > 0 ? ra * 1000 : null);
        }
        return res;
      }
    },
  };
}
