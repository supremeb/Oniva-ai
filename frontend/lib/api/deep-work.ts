/**
 * Deep Work API client — autonomous agent execution with plan-first UX.
 *
 * Uses the shared apiFetch for cookie auth, CSRF protection, and token refresh.
 */

import { apiFetch, API_BASE_URL } from '@/lib/api';
import type {
  DeepWorkArtifact,
  StartEngineRequest,
  StartEngineResponse,
  EnginePlanResponse,
  IntakeResult,
} from '@/lib/types/deep-work';

/** List workspace files */
export async function listWorkspaceFiles(
  sessionId: string,
): Promise<{ files: DeepWorkArtifact[] }> {
  return apiFetch(`/api/v1/deep-work/${sessionId}/workspace`, {
    timeout: 10000,
  });
}

/** Download a workspace file as text */
export async function getWorkspaceFileContent(
  sessionId: string,
  filePath: string,
): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/api/v1/deep-work/${sessionId}/workspace/${filePath}`, {
    credentials: 'include',
  });
  if (!res.ok) throw new Error(`Failed to fetch file: ${res.status}`);
  return res.text();
}

/** Fetch full Deep Work state by conversation ID (returns null on 404) */
export async function getDeepWorkFullState(
  conversationId: string,
): Promise<import('@/lib/stores/deep-work').DeepWorkFullState | null> {
  try {
    return await apiFetch(
      `/api/v1/deep-work/by-conversation/${conversationId}/full-state`,
      { timeout: 10000 },
    );
  } catch (err) {
    // 404 = no deep work for this conversation (not an error)
    if (err instanceof Error && err.message.includes('404')) return null;
    throw err;
  }
}

// ── Deep Work Engine (v2) API ────────────────────────────────

/** Ask clarifier LLM whether to ask questions or auto-proceed */
export async function clarifyQuery(
  query: string,
  availableKbs: string[] = [],
  availableConnectors: string[] = [],
): Promise<IntakeResult> {
  return apiFetch('/api/v1/deep-work/engine/clarify', {
    method: 'POST',
    body: JSON.stringify({
      query,
      available_kbs: availableKbs,
      available_connectors: availableConnectors,
    }),
    timeout: 15000,
  });
}

/** Generate a plan without executing (LLM orchestration can take 15-30s) */
export async function generateEnginePlan(
  query: string,
  enrichedQuery?: string,
): Promise<EnginePlanResponse> {
  return apiFetch('/api/v1/deep-work/engine/plan', {
    method: 'POST',
    body: JSON.stringify({ query, enriched_query: enrichedQuery }),
    timeout: 60000,
  });
}

/** Start a deep work engine session */
export async function startEngine(
  body: StartEngineRequest,
): Promise<StartEngineResponse> {
  return apiFetch('/api/v1/deep-work/engine', {
    method: 'POST',
    body: JSON.stringify(body),
    timeout: 30000,
  });
}

/** Cancel a running engine session */
export async function cancelEngine(
  sessionId: string,
): Promise<{ session_id: string; status: string }> {
  return apiFetch(`/api/v1/deep-work/engine/${sessionId}/cancel`, {
    method: 'POST',
    timeout: 10000,
  });
}

/** Build an EventSource URL for engine SSE events */
export function buildEngineEventsUrl(sessionId: string): string {
  return `${API_BASE_URL}/api/v1/deep-work/engine/${sessionId}/stream`;
}
