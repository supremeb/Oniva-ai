import { apiFetch } from './index';

export interface ConversationListItem {
    id: string;
    source: 'legacy' | 'engine';
    title: string;
    updated_at: string;
    project_id?: string | null;
    legacy_session_id?: string | null;
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
