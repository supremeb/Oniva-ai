/**
 * useDeepWorkChat — API-driven Deep Work state for a chat session.
 *
 * Manages:
 * - Fetching Deep Work state from backend via TanStack Query
 * - Hydrating the Zustand store from the backend response
 * - SSE connection for active sessions
 * - Result saving as assistant message on completion
 */

import { useRef, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useDeepWorkStore, selectIsDeepWorkActive, selectCurrentActivity, selectConversationId } from '@/lib/stores/deep-work';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import { getDeepWorkFullState } from '@/lib/api/deep-work';

export function useDeepWorkChat(currentSessionId: string | null) {
    const queryClient = useQueryClient();
    const addMessageToSession = useChatSessionsStore((s) => s.addMessageToSession);

    // Deep Work store subscriptions
    const deepWorkStatus = useDeepWorkStore((s) => s.status);
    const deepWorkRespondToQuestion = useDeepWorkStore((s) => s.respondToQuestion);
    const isDeepWorkActive = useDeepWorkStore(selectIsDeepWorkActive);
    const deepWorkSessionId = useDeepWorkStore((s) => s.sessionId);
    const currentActivity = useDeepWorkStore(selectCurrentActivity);
    const deepWorkConversationId = useDeepWorkStore(selectConversationId);
    const deepWorkSummary = useDeepWorkStore((s) => s.summary);
    const deepWorkKeyFindings = useDeepWorkStore((s) => s.keyFindings);
    const deepWorkPlanTitle = useDeepWorkStore((s) => s.plan?.title);
    const deepWorkEngineMode = useDeepWorkStore((s) => s.engineMode);

    // ── Fetch Deep Work state from backend on chat open ──────────
    const { data: fullState } = useQuery({
        queryKey: ['deep-work', currentSessionId],
        queryFn: () => getDeepWorkFullState(currentSessionId!),
        enabled: !!currentSessionId,
        staleTime: 30_000,
        retry: false,
    });

    // Hydrate store when backend data arrives or conversation changes.
    // Guard: skip hydration if SSE is active for THIS conversation (SSE is source of truth).
    // No ref-based dedup — the SSE guard is sufficient and avoids stale cache lock-in.
    useEffect(() => {
        if (!currentSessionId || !fullState) return;

        const store = useDeepWorkStore.getState();

        // If SSE is already connected for this conversation, it's the source of truth
        if (store.eventSource && store.conversationId === fullState.conversation_id) {
            return;
        }
        // Disconnect stale SSE from a different conversation
        if (store.eventSource) {
            store.disconnectEvents();
        }

        store.hydrateFromBackend(fullState);

        // Connect SSE for active sessions
        const activeStatuses = ['running', 'executing', 'paused', 'awaiting_input', 'pending'];
        if (activeStatuses.includes(fullState.status)) {
            if (fullState.is_engine) {
                store.connectEngineEvents(fullState.session_id);
            } else {
                store.connectEvents(fullState.session_id);
            }
        }
    }, [fullState, currentSessionId]);

    // Clean up SSE when switching conversations (prevents orphaned connections)
    useEffect(() => {
        return () => {
            const store = useDeepWorkStore.getState();
            if (store.eventSource) {
                store.disconnectEvents();
            }
        };
    }, [currentSessionId]);

    // Derived state: only show deep work UI in the chat session that spawned it
    const isDeepWorkForThisChat = deepWorkConversationId != null && deepWorkConversationId === currentSessionId;
    const isAwaitingDeepWorkInput = deepWorkStatus === 'awaiting_input' && isDeepWorkForThisChat;

    // Refresh sidebar when Deep Work completes
    useEffect(() => {
        if (deepWorkStatus === 'completed' || deepWorkStatus === 'error' || deepWorkStatus === 'stopped') {
            queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
        }
    }, [deepWorkStatus, queryClient]);

    // Save deep work results as an assistant chat message on completion
    const deepWorkResultSavedRef = useRef<string | null>(null);
    const [resultMessage, setResultMessage] = useState<{ role: 'assistant'; content: string } | null>(null);
    useEffect(() => {
        if (deepWorkStatus !== 'completed' || !deepWorkSummary || !isDeepWorkForThisChat) return;
        const sessionId = currentSessionId;
        if (!sessionId) return;
        const dedupeKey = `${deepWorkSessionId}-${deepWorkSummary.slice(0, 50)}`;
        if (deepWorkResultSavedRef.current === dedupeKey) return;
        deepWorkResultSavedRef.current = dedupeKey;

        // For engine sessions, save a brief summary (full report lives in DeepWorkCard)
        // For legacy sessions, save the full summary
        const content = deepWorkEngineMode
            ? `Research on "${deepWorkPlanTitle || 'your query'}" is complete. See the report below.`
            : deepWorkKeyFindings.length > 0
                ? deepWorkSummary + '\n\n**Key Findings:**\n' + deepWorkKeyFindings.map((f) => `- ${f}`).join('\n')
                : deepWorkSummary;

        setResultMessage({ role: 'assistant', content });

        // Check if result message already exists in session store (prevents duplicates on refresh)
        const existingSession = useChatSessionsStore.getState().getSession(sessionId);
        const alreadySaved = existingSession?.messages.some(
            (m) => m.role === 'assistant' && m.content.includes('is complete')
        );
        if (!alreadySaved) {
            addMessageToSession(sessionId, { role: 'assistant', content });
        }
    }, [deepWorkStatus, deepWorkSummary, deepWorkKeyFindings, currentSessionId, deepWorkSessionId, isDeepWorkForThisChat, addMessageToSession, deepWorkEngineMode, deepWorkPlanTitle]);

    return {
        // State
        deepWorkStatus,
        isDeepWorkActive,
        isDeepWorkForThisChat,
        isAwaitingDeepWorkInput,
        currentActivity,
        resultMessage,

        // Actions
        deepWorkRespondToQuestion,
    };
}
