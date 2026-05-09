/**
 * API functions for chat sessions
 */
import { apiFetch } from './index';
import type {
  ChatSession,
  ChatSessionCreate,
  ChatSessionUpdate,
  ChatSessionListResponse,
} from '@/lib/types/chat-panels';

/**
 * Create a new chat session
 */
export async function createChatSession(data: ChatSessionCreate): Promise<ChatSession> {
  return apiFetch<ChatSession>('/api/v1/chat-sessions', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Get chat sessions with optional filters
 */
export async function getChatSessions(params?: {
  context_type?: 'agent' | 'workflow';
  agent_id?: string;
  workflow_id?: string;
  is_draft?: boolean;
  is_pinned?: boolean;
  page?: number;
  page_size?: number;
}): Promise<ChatSessionListResponse> {
  const searchParams = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        searchParams.append(key, String(value));
      }
    });
  }
  const query = searchParams.toString();
  return apiFetch<ChatSessionListResponse>(
    `/api/v1/chat-sessions${query ? `?${query}` : ''}`
  );
}

/**
 * Get a specific chat session
 */
export async function getChatSession(sessionId: string): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/api/v1/chat-sessions/${sessionId}`);
}

/**
 * Update a chat session
 */
export async function updateChatSession(
  sessionId: string,
  data: ChatSessionUpdate
): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/api/v1/chat-sessions/${sessionId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a chat session
 */
export async function deleteChatSession(sessionId: string): Promise<void> {
  return apiFetch<void>(`/api/v1/chat-sessions/${sessionId}`, {
    method: 'DELETE',
  });
}

/**
 * Add a message to a chat session
 */
export async function addMessageToSession(
  sessionId: string,
  message: {
    role: 'user' | 'assistant' | 'system';
    content: string;
    metadata?: Record<string, unknown>;
  }
): Promise<{
  session_id: string;
  message: Record<string, unknown>;
  message_count: number;
}> {
  return apiFetch(`/api/v1/chat-sessions/${sessionId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}

/**
 * Clear all messages from a session
 */
export async function clearSessionMessages(sessionId: string): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/api/v1/chat-sessions/${sessionId}/clear`, {
    method: 'POST',
  });
}

/**
 * Get recent sessions for an agent
 */
export async function getRecentAgentSessions(
  agentId: string,
  limit?: number
): Promise<ChatSessionListResponse> {
  const query = limit ? `?limit=${limit}` : '';
  return apiFetch<ChatSessionListResponse>(
    `/api/v1/chat-sessions/agent/${agentId}/recent${query}`
  );
}

/**
 * Get recent sessions for a workflow
 */
export async function getRecentWorkflowSessions(
  workflowId: string,
  limit?: number
): Promise<ChatSessionListResponse> {
  const query = limit ? `?limit=${limit}` : '';
  return apiFetch<ChatSessionListResponse>(
    `/api/v1/chat-sessions/workflow/${workflowId}/recent${query}`
  );
}
