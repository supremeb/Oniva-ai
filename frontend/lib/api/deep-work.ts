/**
 * Deep Work API client — autonomous agent execution with plan-first UX.
 *
 * Uses the shared apiFetch for cookie auth, CSRF protection, and token refresh.
 */

import { apiFetch, API_BASE_URL } from '@/lib/api';
import type { DeepWorkArtifact } from '@/lib/types/deep-work';

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
