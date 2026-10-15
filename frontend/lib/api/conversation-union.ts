import { apiFetch } from './index';

export interface ConversationListItem {
    id: string;
    source: 'legacy' | 'engine';
    title: string;
    updated_at: string;
    project_id?: string | null;
    legacy_session_id?: string | null;
    pinned?: boolean;
}

export interface ConversationListResponse {
    items: ConversationListItem[];
    total: number;
    page: number;
    page_size: number;
}

/** Union of legacy sessions and engine conversations (`?include=engine`). */
export function getConversationUnion(pageSize = 50): Promise<ConversationListResponse> {
    return apiFetch<ConversationListResponse>(`/api/v1/chat-sessions?include=engine&page_size=${pageSize}`);
}

const engineUrl = (id: string) => `/api/v1/engine/conversations/${encodeURIComponent(id)}`;

/** Rename and/or pin an engine conversation. */
export function updateEngineConversation(
    id: string,
    patch: { title?: string; pinned?: boolean }
): Promise<{ conversationId: string; title: string | null; pinned: boolean }> {
    return apiFetch(engineUrl(id), { method: 'PATCH', body: JSON.stringify(patch) });
}

/** Sidebar "delete": archives the conversation (hidden from the list; engine data is kept). */
export function archiveEngineConversation(id: string): Promise<void> {
    return apiFetch<void>(engineUrl(id), { method: 'DELETE' });
}
