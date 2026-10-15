import { useQuery } from '@tanstack/react-query';
import { getConversationUnion } from '@/lib/api/conversation-union';

/** Engine conversations for the sidebar. Only fetched when `enabled` (engine mode). */
export function useConversationUnion(enabled: boolean) {
    return useQuery({
        queryKey: ['chat-sessions', 'union'],
        queryFn: () => getConversationUnion(50),
        select: (data) => data.items,
        enabled,
    });
}
