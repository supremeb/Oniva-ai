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
import { isEngineRuntimeSession, useEngineSessionStore } from '@/components/deep-work/engineSession';

export function useDeepWorkChat(currentSessionId: string | null) {
    const queryClient = useQueryClient();
    const addMessageToSession = useChatSessionsStore((s) => s.addMessageToSession);

    // Deep Work store subscriptions
    const deepWorkStatus = useDeepWorkStore((s) => s.status);
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

        // Engine-runtime sessions: no hydration and no EventSource. The card reads the
        // run from its watch stream; only the ids are needed. Not gated on the flag:
        // existing engine runs stay readable after a tenant switches back (R3).
        if (isEngineRuntimeSession(fullState)) {
            const prev = useEngineSessionStore.getState().session;
            if (!prev || prev.sessionId !== fullState.session_id || prev.chatId !== currentSessionId) {
                useEngineSessionStore.getState().setSession({
                    sessionId: fullState.session_id,
                    conversationId: fullState.conversation_id,
                    chatId: currentSessionId,
                    status: fullState.status,
                    error: fullState.error ?? null,
                    planTitle: fullState.plan?.title ?? null,
                });
            }
            return;
        }
        if (useEngineSessionStore.getState().session) useEngineSessionStore.getState().setSession(null);

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
            // Only the legacy v2 pipeline streams; v1 executor sessions are history (read-only).
            if (fullState.is_engine) store.connectEngineEvents(fullState.session_id);
        }
    }, [fullState, currentSessionId]);

    // Clean up SSE when switching conversations (prevents orphaned connections)
    useEffect(() => {
        return () => {
            const store = useDeepWorkStore.getState();
            if (store.eventSource) {
                store.disconnectEvents();
            }
            useEngineSessionStore.getState().setSession(null);
        };
    }, [currentSessionId]);

    // Engine session for this chat, if any.
    const engineSession = useEngineSessionStore((s) => s.session);
    const engineTerminal = useEngineSessionStore((s) => s.terminal);
    const isEngineForThisChat = engineSession != null && engineSession.chatId === currentSessionId;

    // Derived state: only show deep work UI in the chat session that spawned it
    const isDeepWorkForThisChat =
        isEngineForThisChat || (deepWorkConversationId != null && deepWorkConversationId === currentSessionId);

    // Refresh sidebar when Deep Work completes
    useEffect(() => {
        if (deepWorkStatus === 'completed' || deepWorkStatus === 'error' || deepWorkStatus === 'stopped') {
            queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
        }
    }, [deepWorkStatus, queryClient]);

    const [resultMessage, setResultMessage] = useState<{ role: 'assistant'; content: string } | null>(null);

    // Engine sessions: the run's own terminal state (observed live by the card) drives the
    // result message, once per session. A rejoin of a finished run never reports a terminal
    // transition, so nothing is duplicated and no message text is inspected.
    const engineSavedRef = useRef<string | null>(null);
    useEffect(() => {
        if (!engineTerminal || !isEngineForThisChat || !engineSession || !currentSessionId) return;
        if (engineTerminal.sessionId !== engineSession.sessionId) return;
        queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
        if (engineTerminal.status !== 'completed') return;
        if (engineSavedRef.current === engineSession.sessionId) return;
        engineSavedRef.current = engineSession.sessionId;
        const content = `Research on "${engineSession.planTitle || 'your query'}" is complete. See the report below.`;
        setResultMessage({ role: 'assistant', content });
        addMessageToSession(currentSessionId, { role: 'assistant', content });
    }, [engineTerminal, engineSession, isEngineForThisChat, currentSessionId, queryClient, addMessageToSession]);

    // Save deep work results as an assistant chat message on completion
    const deepWorkResultSavedRef = useRef<string | null>(null);
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
        currentActivity,
        resultMessage,
        engineSession: isEngineForThisChat ? engineSession : null,
    };
}
