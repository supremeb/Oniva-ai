import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
    archiveEngineConversation,
    updateEngineConversation,
    type ConversationListItem,
    type ConversationListResponse,
} from '@/lib/api/conversation-union';

const UNION_KEY = ['chat-sessions', 'union'] as const;

/** Optimistically rewrite the cached union list; returns a rollback. */
function useOptimisticUnion() {
    const qc = useQueryClient();
    return async (apply: (items: ConversationListItem[]) => ConversationListItem[]) => {
        await qc.cancelQueries({ queryKey: UNION_KEY });
        const previous = qc.getQueryData<ConversationListResponse>(UNION_KEY);
        if (previous) qc.setQueryData<ConversationListResponse>(UNION_KEY, { ...previous, items: apply(previous.items) });
        return { previous };
    };
}

export function useUpdateEngineConversation() {
    const qc = useQueryClient();
    const optimistic = useOptimisticUnion();
    return useMutation({
        mutationFn: ({ id, ...patch }: { id: string; title?: string; pinned?: boolean }) => updateEngineConversation(id, patch),
        onMutate: ({ id, ...patch }) =>
            optimistic((items) => items.map((i) => (i.id === id && i.source === 'engine' ? { ...i, ...patch } : i))),
        onError: (_e, _v, ctx) => {
            if (ctx?.previous) qc.setQueryData(UNION_KEY, ctx.previous);
        },
        onSettled: () => qc.invalidateQueries({ queryKey: ['chat-sessions'] }),
    });
}

export function useArchiveEngineConversation() {
    const qc = useQueryClient();
    const optimistic = useOptimisticUnion();
    return useMutation({
        mutationFn: (id: string) => archiveEngineConversation(id),
        onMutate: (id) => optimistic((items) => items.filter((i) => !(i.id === id && i.source === 'engine'))),
        onError: (_e, _v, ctx) => {
            if (ctx?.previous) qc.setQueryData(UNION_KEY, ctx.previous);
        },
        onSettled: () => qc.invalidateQueries({ queryKey: ['chat-sessions'] }),
    });
}
