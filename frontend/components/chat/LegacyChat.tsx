'use client';

import { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useQueryClient } from '@tanstack/react-query';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import { modelSupportsThinking } from '@/lib/models';
import type { MessagePart } from '@/lib/models';
import { API_BASE_URL } from '@/lib/api';
import type { Skill } from '@/lib/api/skills';
import { useDeepWorkStore } from '@/lib/stores/deep-work';
import { useChatSessionDetail } from '@/hooks/useChatSessions';
import { useChatUIStore } from '@/lib/stores/chat-ui';
import { RightPanel, type FileMeta } from './RightPanel';
import type { FileNode } from './FileTree';
import { createChatSession } from '@/lib/api/chat-sessions';
import { BUILTIN_AGENT_UUIDS, isBuiltinAgentId } from '@/lib/builtin-agents';
import { useDeepWorkChat } from '@/hooks/useDeepWorkChat';
import { useDeepWorkIntake } from '@/hooks/useDeepWorkIntake';
import { useStreamingParts } from '@/hooks/useStreamingParts';
import { ArtifactModal } from './shared/ArtifactModal';
import type { ArtifactModalData } from './shared/data/FilePreview';
import { useAgentInfo } from '@/hooks/useAgentInfo';
import { useProjectsStore } from '@/lib/stores/projects';
import { executeWorkflow } from '@/lib/api/agent-studio';
import { parseChatSSEStream as parseWorkflowSSE } from '@/lib/sse/parseChatSSE';
import { ChatShell } from './ChatShell';
import { MessageList, type ActiveToolCall, type ChatMessage } from './MessageList';
import { Composer, WelcomeComposer } from './Composer';
import { buildSelectionPopovers } from './ChatSelections';

const REQUEST_TIMEOUT_MS = 180000; // 3 minutes for streaming responses with tool calls
const CHAT_API_URL = `${API_BASE_URL}/api/v1/chat`;

let messageIdCounter = 0;
function generateMessageId(): string {
    return `msg-${Date.now()}-${++messageIdCounter}`;
}

interface LegacyChatProps {
    projectId?: string;
}

/** Legacy chat: all streaming, session and deep-work logic, composed from the chat UI pieces. */
export function LegacyChat({ projectId }: LegacyChatProps = {}) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const viewportRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const queryClient = useQueryClient();
    const {
        currentSessionId,
        getSession,
        addMessageToSession,
        updateLastMessageInSession,
        updateLastAssistantInSession,
        createSession,
        setCurrentSession,
        selectedModel,
        setSelectedModel,
        thinkingEnabled,
        setThinkingEnabled,
        toggleConnector,
        deleteSession,
        updateSessionTitle,
        getActiveSources,
        toggleSource,
        migrateSession,
        consumePendingInput,
    } = useChatSessionsStore(useShallow((s) => ({
        currentSessionId: s.currentSessionId,
        getSession: s.getSession,
        addMessageToSession: s.addMessageToSession,
        updateLastMessageInSession: s.updateLastMessageInSession,
        updateLastAssistantInSession: s.updateLastAssistantInSession,
        createSession: s.createSession,
        setCurrentSession: s.setCurrentSession,
        selectedModel: s.selectedModel,
        setSelectedModel: s.setSelectedModel,
        thinkingEnabled: s.thinkingEnabled,
        setThinkingEnabled: s.setThinkingEnabled,
        toggleConnector: s.toggleConnector,
        deleteSession: s.deleteSession,
        updateSessionTitle: s.updateSessionTitle,
        getActiveSources: s.getActiveSources,
        toggleSource: s.toggleSource,
        migrateSession: s.migrateSession,
        consumePendingInput: s.consumePendingInput,
    })));

    const [input, setInput] = useState('');
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [isStreaming, setIsStreaming] = useState(false);
    const [streamingMessageId, setStreamingMessageId] = useState<string | undefined>(undefined);
    const [deepWorkMode, setDeepWorkMode] = useState(false);
    const [deepWorkSuggestion, setDeepWorkSuggestion] = useState<string | null>(null);
    const lastUserMessageRef = useRef<string>('');
    const [activeToolCalls, setActiveToolCalls] = useState<ActiveToolCall[]>([]);
    const [selectedSkills, setSelectedSkills] = useState<Skill[]>([]);
    const [skillPickerOpen, setSkillPickerOpen] = useState(false);
    const [slashQuery, setSlashQuery] = useState('');
    const abortControllerRef = useRef<AbortController | null>(null);
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);
    const streamingForSessionRef = useRef<string | null>(null);
    const loadedForSessionRef = useRef<string | null>(null);
    const currentSessionIdRef = useRef(currentSessionId);
    currentSessionIdRef.current = currentSessionId;
    const [streamStale, setStreamStale] = useState(false);
    const lastEventTimeRef = useRef<number>(0);

    // Artifact modal state
    const [artifactModal, setArtifactModal] = useState<(ArtifactModalData & { open: boolean }) | null>(null);
    const handleOpenModal = useCallback((data: ArtifactModalData) => {
        setArtifactModal({ ...data, open: true });
    }, []);

    // Rich streaming parts — accumulates MessagePart[] in parallel with word-queue
    const {
        richParts: streamingRichParts,
        handlers: richPartHandlers,
        reset: resetRichParts,
        finalize: finalizeRichParts,
        getSnapshot: getStreamingSnapshot,
        textContent: richTextContent,
        reasoning: richReasoning,
        sources: richSources,
        files: streamingFiles,
        toolCallMeta: streamingToolCallMeta,
        workflowPreview,
        workflowProgress,
        workflowResult,
    } = useStreamingParts();

    // Ref removed: persistence now uses getStreamingSnapshot() which reads refs directly

    // Always fetch backend session for persistence (backend is source of truth)
    const localSession = currentSessionId ? getSession(currentSessionId) : undefined;
    const isBackendSession = !!currentSessionId && !localSession;
    const { data: backendSession } = useChatSessionDetail(currentSessionId);

    // Resolve agent display info for the header
    const agentInfo = useAgentInfo(localSession?.agentId ?? null);

    // Resolve project info for project-scoped chats
    const { getProject, loadProject } = useProjectsStore(useShallow((s) => ({
        getProject: s.getProject,
        loadProject: s.loadProject,
    })));
    const project = projectId ? getProject(projectId) : undefined;
    useEffect(() => {
        if (projectId && !project) {
            loadProject(projectId);
        }
    }, [projectId, project, loadProject]);

    // Derive active connector IDs from current session
    // Direct store subscription ensures re-render when connector IDs change
    // (useShallow on getSession doesn't trigger re-render since function ref is stable)
    const currentSession = localSession;
    const activeConnectorIds = useChatSessionsStore(useShallow(
        (s) => s.sessions.find((sess) => sess.id === s.currentSessionId)?.activeConnectorIds ?? []
    ));

    // Derive active sources for current session
    // Subscribe to the raw activeSources map so re-renders fire when toggleSource updates it
    const allActiveSources = useChatSessionsStore((s) => s.activeSources);
    const storeCurrentSessionId = useChatSessionsStore((s) => s.currentSessionId);
    const activeSources = allActiveSources[storeCurrentSessionId ?? ''] ?? { webSearchEnabled: false, specificSites: [], activeKnowledgeBaseIds: [] };

    // Deep work files, sources, and pending question for sidebar bridging
    const deepWorkFiles = useDeepWorkStore(s => s.workspaceFiles);
    const deepWorkSources = useDeepWorkStore(s => s.sources);

    // Build deduplicated FileNode[] + metadata map for the right panel
    const { allFiles, fileMeta } = useMemo(() => {
        const seen = new Map<string, { fp: MessagePart; index: number }>();
        const collect = (part: MessagePart) => {
            if (part.type !== 'file') return;
            const name = (part as any).filename || 'untitled';
            if (!seen.has(name)) {
                seen.set(name, { fp: part, index: seen.size });
            }
        };
        // Streaming files first (latest), then persisted
        for (const part of streamingFiles) collect(part);
        for (const msg of messages) {
            if (msg.richParts) {
                for (const part of msg.richParts) collect(part);
            }
        }
        // Bridge deep work workspace files
        for (const wf of deepWorkFiles) {
            if (wf.action !== 'delete' && !seen.has(wf.path)) {
                seen.set(wf.path, {
                    fp: {
                        type: 'file',
                        filename: wf.path.split('/').pop() ?? wf.path,
                        size: wf.size,
                        fileType: 'application/octet-stream',
                    } as MessagePart,
                    index: seen.size,
                });
            }
        }
        const files: FileNode[] = [];
        const meta: Record<string, FileMeta> = {};
        for (const [name, { fp }] of seen) {
            files.push({
                name,
                path: name,
                type: 'file' as const,
                size: (fp as any).size,
            });
            meta[name] = {
                content: (fp as any).content,
                downloadUrl: (fp as any).downloadUrl,
                mimeType: (fp as any).fileType,
                size: (fp as any).size,
            };
        }
        return { allFiles: files, fileMeta: meta };
    }, [streamingFiles, messages, deepWorkFiles]);

    // Handle file selection in the right sidebar
    const handleFileSelect = useCallback((path: string) => {
        useChatUIStore.getState().openArtifact(path);
    }, []);

    // Merge regular chat + deep work sources for the sidebar
    const allSources = useMemo(
        () => [...richSources, ...deepWorkSources],
        [richSources, deepWorkSources]
    );

    // Ensure a session exists for source operations (create lazily).
    // Also fires a fire-and-forget backend call so the ChatSession DB row exists
    // immediately — prevents empty sessions from disappearing after page refresh.
    const ensureSessionId = useCallback(() => {
        if (currentSessionId) return currentSessionId;
        const id = createSession();
        setCurrentSession(id);

        // Determine agent_id: only pass for custom (non-builtin) agents
        const session = getSession(id);
        const agentId = session?.agentId && !isBuiltinAgentId(session.agentId)
            ? session.agentId
            : undefined;

        // Fire-and-forget: create the DB row without blocking the caller
        createChatSession({
            context_type: 'agent',
            title: 'New Chat',
            agent_id: agentId,
            is_draft: true,
        }).catch(() => {
            // Silently ignore — the session will be created on first message send
            // via persist_session() in chat_service.py as a fallback
        });

        return id;
    }, [currentSessionId, createSession, setCurrentSession, getSession]);

    const handleToggleWebSearch = useCallback(() => {
        const sid = ensureSessionId();
        toggleSource(sid, 'web');
    }, [ensureSessionId, toggleSource]);

    const handleAddSite = useCallback((url: string) => {
        const sid = ensureSessionId();
        toggleSource(sid, 'site', url);
    }, [ensureSessionId, toggleSource]);

    const handleRemoveSite = useCallback((url: string) => {
        const sid = ensureSessionId();
        toggleSource(sid, 'site', url);
    }, [ensureSessionId, toggleSource]);

    const handleToggleKnowledgeBase = useCallback((kbId: string) => {
        const sid = ensureSessionId();
        toggleSource(sid, 'kb', kbId);
    }, [ensureSessionId, toggleSource]);

    const toggleDeepWork = useCallback(() => {
        const newValue = !deepWorkMode;
        setDeepWorkMode(newValue);
        if (newValue) {
            // Auto-enable web search when entering deep work mode
            const sid = ensureSessionId();
            const sources = getActiveSources(sid);
            if (!sources.webSearchEnabled) {
                toggleSource(sid, 'web');
            }
        } else {
            // Reset deep work state when toggling off during clarification
            const dwStatus = useDeepWorkStore.getState().status;
            if (dwStatus === 'clarifying') {
                useDeepWorkStore.getState().reset();
            }
        }
    }, [deepWorkMode, ensureSessionId, getActiveSources, toggleSource]);

    const thinkingSupported = selectedModel ? modelSupportsThinking(selectedModel) : false;
    const toggleThinking = useCallback(() => {
        setThinkingEnabled(!thinkingEnabled);
    }, [thinkingEnabled, setThinkingEnabled]);

    // Slash command detection for skill picker
    const handleInputChange = useCallback((value: string) => {
        setInput(value);
        const cursorPos = inputRef.current?.selectionStart ?? value.length;
        const textBeforeCursor = value.slice(0, cursorPos);
        const match = textBeforeCursor.match(/(?:^|\s)(\/\S*)$/);
        if (match) {
            setSlashQuery(match[1].slice(1)); // remove leading /
            setSkillPickerOpen(true);
        } else if (skillPickerOpen) {
            setSkillPickerOpen(false);
        }
    }, [skillPickerOpen]);

    // Handle skill selection from picker
    const handleSkillSelect = useCallback((skill: Skill) => {
        // Remove the /query text from input
        const cursorPos = inputRef.current?.selectionStart ?? input.length;
        const textBeforeCursor = input.slice(0, cursorPos);
        const match = textBeforeCursor.match(/(?:^|\s)(\/\S*)$/);
        if (match) {
            const start = cursorPos - match[1].length;
            // Also remove any preceding whitespace if / was after a space
            const cleanStart = start > 0 && input[start - 1] === ' ' ? start - 1 : start;
            const newInput = input.slice(0, cleanStart) + input.slice(cursorPos);
            setInput(newInput.trim());
        }
        setSelectedSkills((prev) =>
            prev.some((s) => s.id === skill.id) ? prev : [...prev, skill]
        );
        setSlashQuery('');
        setSkillPickerOpen(false);
    }, [input]);

    const handleRemoveSkill = useCallback((skillId: string) => {
        setSelectedSkills((prev) => prev.filter((s) => s.id !== skillId));
    }, []);

    const handleToggleConnector = useCallback((connectorId: string) => {
        const sid = ensureSessionId();
        toggleConnector(sid, connectorId);
    }, [ensureSessionId, toggleConnector]);

    // ── Deep Work Engine Flow ──────────────────────────────

    const { startIntake: startDeepWorkIntake } = useDeepWorkIntake(projectId);

    const handleDeepWorkSubmit = useCallback(async (text: string) => {
        const localSessionId = ensureSessionId();
        setError(null);

        // Show user query as a message
        const userMessage: ChatMessage = {
            id: generateMessageId(),
            role: 'user',
            content: text,
        };
        setMessages((prev) => [...prev, userMessage]);
        addMessageToSession(localSessionId, { role: 'user', content: text });

        // Persist session to backend and capture the backend-generated UUID.
        const session = getSession(localSessionId);
        const agentId = session?.agentId || BUILTIN_AGENT_UUIDS['general'];
        const title = text.slice(0, 50) + (text.length > 50 ? '...' : '');

        let activeSessionId = localSessionId;
        try {
            const backendSession = await createChatSession({
                title,
                context_type: 'agent',
                agent_id: agentId === BUILTIN_AGENT_UUIDS['general'] ? undefined : agentId,
                messages: [{ role: 'user', content: text }],
            });
            migrateSession(localSessionId, backendSession.id);
            activeSessionId = backendSession.id;
            queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
        } catch {
            // Non-critical: continue with local UUID if backend creation fails
        }

        // Run intake clarification first — may ask questions or auto-skip to planning
        const activeSrc = getActiveSources(activeSessionId);
        const activeSession = getSession(activeSessionId);
        const kbIds = activeSrc.activeKnowledgeBaseIds || [];
        const connectorIds = activeSession?.activeConnectorIds ?? [];
        void startDeepWorkIntake(text, activeSessionId, kbIds, connectorIds);
    }, [ensureSessionId, addMessageToSession, getSession, getActiveSources, migrateSession, queryClient, startDeepWorkIntake]);

    // Load messages when switching sessions or on page refresh.
    // IMPORTANT: This effect must NOT depend on isStreaming/isLoading — those cause
    // re-runs when streaming ends, overwriting the live messages from sendMessage.
    // Instead, use streamingForSessionRef (a ref) as a batching-immune guard.
    useEffect(() => {
        // Block while ANY streaming is active (ref-based, immune to React batching).
        // sendMessage sets this ref synchronously before any state updates.
        if (streamingForSessionRef.current) return;

        // Already loaded messages for this session — skip React Query refetches
        if (loadedForSessionRef.current === currentSessionId) return;

        if (currentSessionId) {
            const localSession = getSession(currentSessionId);
            if (localSession?.messages?.length) {
                loadedForSessionRef.current = currentSessionId;
                setMessages(localSession.messages.map((m) => ({
                    id: m.id,
                    role: m.role,
                    content: m.content,
                    reasoning: m.reasoning,
                    richParts: m.richParts,
                })));
            } else if (backendSession?.messages?.length && backendSession.id === currentSessionId) {
                loadedForSessionRef.current = currentSessionId;
                setMessages((backendSession.messages)
                    .filter((m) => m.role === 'user' || m.role === 'assistant')
                    .map((m, i) => {
                        let parts = m.metadata?.parts as MessagePart[] | undefined;
                        if (parts?.length && m.content && !parts.some((p: any) => p.type === 'text')) {
                            parts = [...parts, { type: 'text', content: m.content, isStreaming: false } as MessagePart];
                        }
                        return {
                            id: `backend-${i}`,
                            role: m.role as 'user' | 'assistant',
                            content: m.content,
                            reasoning: m.metadata?.reasoning as string | undefined,
                            richParts: parts,
                        };
                    }));
            } else {
                // No data yet — leave loadedForSessionRef unset so the effect
                // re-runs when backendSession arrives (page refresh case)
                setMessages([]);
            }
        } else {
            loadedForSessionRef.current = null;
            setMessages([]);
        }
    }, [currentSessionId, getSession, backendSession]);

    // Reset transient state when switching sessions
    const prevSessionRef = useRef(currentSessionId);
    useEffect(() => {
        if (prevSessionRef.current !== currentSessionId) {
            prevSessionRef.current = currentSessionId;
            loadedForSessionRef.current = null;
            setError(null);
            deepWorkResultAppendedRef.current = null;
            useDeepWorkStore.getState().disconnectEvents();

            // Only reset streaming UI if we're NOT returning to the actively streaming session
            if (streamingForSessionRef.current !== currentSessionId) {
                setActiveToolCalls([]);
                resetRichParts();
            }
        }
    }, [currentSessionId, resetRichParts]);

    const clearRequestTimeout = useCallback(() => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
            timeoutRef.current = null;
        }
    }, []);

    const sendMessage = useCallback(async (text: string) => {
        if (!text.trim() || isLoading) return;

        lastUserMessageRef.current = text;

        // Create a session if none exists
        let sessionId = currentSessionId;
        if (!sessionId) {
            sessionId = createSession();
            setCurrentSession(sessionId);
        }

        setError(null);
        streamingForSessionRef.current = sessionId;

        const userMessage: ChatMessage = {
            id: generateMessageId(),
            role: 'user',
            content: text,
        };

        setMessages((prev) => [...prev, userMessage]);
        addMessageToSession(sessionId, { role: 'user', content: text });
        setIsLoading(true);

        const assistantMessageId = generateMessageId();
        const assistantMessage: ChatMessage = {
            id: assistantMessageId,
            role: 'assistant',
            content: '',
        };
        // Defer adding assistant placeholder until stream actually starts
        // to avoid a flash of empty content before the LLM responds.
        let assistantPlaceholderInserted = false;
        const insertAssistantPlaceholder = () => {
            if (assistantPlaceholderInserted) return;
            assistantPlaceholderInserted = true;
            setMessages((prev) => [...prev, assistantMessage]);
            addMessageToSession(sessionId!, { role: 'assistant', content: '' });
            setStreamingMessageId(assistantMessageId);
        };

        abortControllerRef.current = new AbortController();

        timeoutRef.current = setTimeout(() => {
            abortControllerRef.current?.abort();
            setError('Request timed out. The server may be unavailable.');
            setIsLoading(false);
            setIsStreaming(false);
            setStreamingMessageId(undefined);
        }, REQUEST_TIMEOUT_MS);

        // Hoisted so they're accessible in both try and catch
        let flushIntervalId: ReturnType<typeof setInterval> | null = null;
        let handleBeforeUnload: (() => void) | null = null;
        let fullContent = '';
        let fullReasoning = '';
        let backendAssignedSessionId: string | null = null;

        try {
            const apiMessages = [...messages, userMessage].map((m) => ({
                role: m.role,
                content: m.content,
                ...(m.richParts?.length && { metadata: { parts: m.richParts } }),
            }));

            // Get agentId from current session
            // Default to "general" for conversational chat
            const session = sessionId ? getSession(sessionId) : null;
            const agentId = session?.agentId || BUILTIN_AGENT_UUIDS['general'];

            const headers: Record<string, string> = { 'Content-Type': 'application/json' };

            // Add CSRF token for cookie-based auth
            const csrfMatch = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
            if (csrfMatch) {
                headers['X-CSRF-Token'] = decodeURIComponent(csrfMatch[1]);
            }

            // Connector and skill IDs for this request
            const connectorIds = session?.activeConnectorIds ?? [];
            const skillIds = selectedSkills.map((s) => s.id);

            // Source selections — always read from store (sessionId is guaranteed above)
            const sources = getActiveSources(sessionId);

            const body = JSON.stringify({
                messages: apiMessages,
                sessionId,
                ...(selectedModel && { model: selectedModel }),
                agentId,
                enhancedMode: true,
                streamMode: 'hybrid',
                enableThinking: useChatSessionsStore.getState().thinkingEnabled,
                ...(connectorIds.length > 0 && { active_connector_ids: connectorIds }),
                ...(skillIds.length > 0 && { active_skill_ids: skillIds }),
                ...(sources.webSearchEnabled && { web_search_enabled: true }),
                ...(sources.specificSites.length && { specific_sites: sources.specificSites }),
                ...(sources.activeKnowledgeBaseIds.length && { active_knowledge_base_ids: sources.activeKnowledgeBaseIds }),
                ...(projectId && { projectId }),
            });

            const response = await fetch(CHAT_API_URL, {
                method: 'POST',
                headers,
                credentials: 'include',
                body,
                signal: abortControllerRef.current.signal,
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`HTTP ${response.status}: ${errorText}`);
            }

            const reader = response.body?.getReader();
            if (!reader) {
                throw new Error('No response body');
            }

            setIsStreaming(true);
            setStreamStale(false);
            lastEventTimeRef.current = Date.now();
            // Insert assistant placeholder now that the stream is established
            insertAssistantPlaceholder();

            // Periodic flush: persist in-progress content to the store every 5s
            // so the message survives session switches and page navigation
            flushIntervalId = setInterval(() => {
                if (fullContent && sessionId) {
                    updateLastAssistantInSession(sessionId, { content: fullContent });
                }
            }, 5000);

            // Save in-progress content on tab close
            handleBeforeUnload = () => {
                if (fullContent && sessionId) {
                    updateLastAssistantInSession(sessionId, {
                        content: fullContent,
                        ...(fullReasoning ? { reasoning: fullReasoning } : {}),
                    });
                }
            };
            window.addEventListener('beforeunload', handleBeforeUnload);

            // Word-queue streaming: accumulate incoming deltas in a queue,
            // then drain word-by-word via setTimeout for visible streaming
            // regardless of TCP batching / gzip buffering.
            const wordQueue = { current: '' };
            let drainTimerId: ReturnType<typeof setTimeout> | null = null;
            let streamDone = false;
            let onQueueDrained: (() => void) | null = null;

            const drainWords = () => {
                drainTimerId = null;
                if (!wordQueue.current) {
                    // Queue is empty — if stream ended, notify completion
                    if (streamDone && onQueueDrained) {
                        onQueueDrained();
                        onQueueDrained = null;
                    }
                    return;
                }

                // Pull 2-3 words per tick for natural pacing
                let pulled = '';
                for (let i = 0; i < 3 && wordQueue.current; i++) {
                    const match = wordQueue.current.match(/^\S+\s*/);
                    if (match) {
                        pulled += match[0];
                        wordQueue.current = wordQueue.current.slice(match[0].length);
                    } else {
                        // Leading whitespace or special chars
                        pulled += wordQueue.current;
                        wordQueue.current = '';
                        break;
                    }
                }

                if (pulled) {
                    fullContent += pulled;
                    // Only update visual state if user is still viewing this session
                    if (currentSessionIdRef.current === sessionId) {
                        setMessages((prev) =>
                            prev.map((m) =>
                                m.id === assistantMessageId
                                    ? { ...m, content: fullContent }
                                    : m
                            )
                        );
                    }
                }

                if (wordQueue.current) {
                    drainTimerId = setTimeout(drainWords, 20);
                } else if (streamDone && onQueueDrained) {
                    onQueueDrained();
                    onQueueDrained = null;
                }
            };

            const flushContent = () => {
                if (drainTimerId) { clearTimeout(drainTimerId); drainTimerId = null; }
                if (wordQueue.current) {
                    fullContent += wordQueue.current;
                    wordQueue.current = '';
                }
                if (currentSessionIdRef.current === sessionId) {
                    setMessages((prev) =>
                        prev.map((m) =>
                            m.id === assistantMessageId
                                ? { ...m, content: fullContent }
                                : m
                        )
                    );
                }
            };

            const pendingReasoningRef = { current: '' };
            let reasoningRafId = 0;

            const flushReasoning = () => {
                const flushed = pendingReasoningRef.current;
                pendingReasoningRef.current = '';
                reasoningRafId = 0;
                fullReasoning += flushed;
                if (currentSessionIdRef.current === sessionId) {
                    setMessages((prev) =>
                        prev.map((m) =>
                            m.id === assistantMessageId
                                ? { ...m, reasoning: fullReasoning }
                                : m
                        )
                    );
                }
            };

            const { parseChatSSEStream } = await import('@/lib/sse/parseChatSSE');

            // Reset rich parts accumulator for new message
            resetRichParts();

            try {
                await parseChatSSEStream(reader, {
                    onContentDelta: (delta) => {
                        lastEventTimeRef.current = Date.now();
                        // Word-queue (flat content)
                        wordQueue.current += delta;
                        if (!drainTimerId) {
                            drainTimerId = setTimeout(drainWords, 0);
                        }
                        // Rich parts (structured)
                        richPartHandlers.onContentDelta?.(delta);
                    },
                    onReasoningDelta: (content) => {
                        lastEventTimeRef.current = Date.now();
                        pendingReasoningRef.current += content;
                        if (!reasoningRafId) {
                            reasoningRafId = requestAnimationFrame(flushReasoning);
                        }
                        richPartHandlers.onReasoningDelta?.(content);
                    },
                    onThinkingDelta: (delta) => {
                        lastEventTimeRef.current = Date.now();
                        richPartHandlers.onThinkingDelta?.(delta);
                    },
                    onToolCallStart: (toolName, toolCallId, label, message, args, producesFile) => {
                        lastEventTimeRef.current = Date.now();
                        if (currentSessionIdRef.current === sessionId) {
                            setActiveToolCalls(prev => [...prev, {
                                id: toolCallId || `tc-${Date.now()}`,
                                toolName,
                                status: 'running',
                            }]);
                        }
                        richPartHandlers.onToolCallStart?.(toolName, toolCallId, label, message, args, producesFile);
                    },
                    onToolCallProgress: (toolName, label, message) => {
                        richPartHandlers.onToolCallProgress?.(toolName, label, message);
                    },
                    onToolCallComplete: (data) => {
                        lastEventTimeRef.current = Date.now();
                        if (currentSessionIdRef.current === sessionId) {
                            setActiveToolCalls(prev => prev.map(tc =>
                                (data.toolCallId && tc.id === data.toolCallId)
                                    ? { ...tc, status: data.success ? 'success' : 'error' }
                                    : tc
                            ));
                        }
                        richPartHandlers.onToolCallComplete?.(data);
                    },
                    onSource: (source) => {
                        lastEventTimeRef.current = Date.now();
                        richPartHandlers.onSource?.(source);
                    },
                    onInfoMessage: (message, title) => {
                        lastEventTimeRef.current = Date.now();
                        richPartHandlers.onInfoMessage?.(message, title);
                    },
                    onPart: (partData) => {
                        lastEventTimeRef.current = Date.now();
                        richPartHandlers.onPart?.(partData);
                    },
                    onDeepWorkSuggested: (reasoning) => {
                        setDeepWorkSuggestion(reasoning);
                    },
                    onWorkflowPreview: (data) => {
                        lastEventTimeRef.current = Date.now();
                        richPartHandlers.onWorkflowPreview?.(data);
                    },
                    onWorkflowProgress: (data) => {
                        lastEventTimeRef.current = Date.now();
                        richPartHandlers.onWorkflowProgress?.(data);
                    },
                    onWorkflowComplete: (data) => {
                        lastEventTimeRef.current = Date.now();
                        richPartHandlers.onWorkflowComplete?.(data);
                    },
                    onError: (message) => {
                        richPartHandlers.onError?.(message);
                        throw new Error(message || 'Server error');
                    },
                    onDone: (doneData) => {
                        if (reasoningRafId) { cancelAnimationFrame(reasoningRafId); flushReasoning(); }
                        richPartHandlers.onDone?.(doneData);
                        finalizeRichParts();

                        // Let the word queue drain naturally so the user
                        // sees word-by-word streaming. Run post-done cleanup
                        // once the queue is empty.
                        const finalize = () => {
                            setActiveToolCalls([]);
                            if (doneData.sessionId && doneData.sessionId !== sessionId) {
                                backendAssignedSessionId = doneData.sessionId;
                            }
                            queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
                            queryClient.invalidateQueries({ queryKey: ['chat-session', doneData.sessionId || sessionId] });
                        };

                        streamDone = true;
                        if (!wordQueue.current) {
                            // Queue already empty — finalize immediately
                            finalize();
                        } else {
                            // Queue still draining — finalize when empty
                            onQueueDrained = finalize;
                            // Ensure drain timer is running
                            if (!drainTimerId) {
                                drainTimerId = setTimeout(drainWords, 0);
                            }
                        }
                    },
                }, abortControllerRef.current?.signal);
            } finally {
                if (reasoningRafId) cancelAnimationFrame(reasoningRafId);
            }

            // Wait for the word queue to finish draining before
            // saving the session (so fullContent is complete).
            if (wordQueue.current && drainTimerId) {
                await new Promise<void>((resolve) => {
                    const prev = onQueueDrained;
                    onQueueDrained = () => { prev?.(); resolve(); };
                });
            }
            // Safety: flush anything still remaining
            if (wordQueue.current) {
                fullContent += wordQueue.current;
                wordQueue.current = '';
            }
            if (drainTimerId) { clearTimeout(drainTimerId); drainTimerId = null; }
            if (pendingReasoningRef.current) {
                fullReasoning += pendingReasoningRef.current;
                pendingReasoningRef.current = '';
            }

            // Read finalized snapshot directly from refs (React state may be stale
            // because finalize()'s setState hasn't re-rendered yet).
            const snapshot = getStreamingSnapshot();
            const finalRichParts = snapshot.richParts.map(part => {
                if (part.type === 'text') return { ...part, isStreaming: false };
                if (part.type === 'thinking') return { ...part, isThinking: false };
                return part;
            });
            const finalSources = snapshot.sources;

            // Snapshot finalized richParts + sources into the assistant message for persistence
            if (currentSessionIdRef.current === sessionId) {
                setMessages((prev) =>
                    prev.map((m) =>
                        m.id === assistantMessageId
                            ? {
                                ...m,
                                content: fullContent,
                                reasoning: fullReasoning || undefined,
                                richParts: finalRichParts.length ? [...finalRichParts] : undefined,
                                sources: finalSources.length ? [...finalSources] : undefined,
                            }
                            : m
                    )
                );
            }

            // Update the assistant placeholder in the session store with final content
            if (sessionId) {
                updateLastAssistantInSession(sessionId, {
                    content: fullContent,
                    ...(fullReasoning ? { reasoning: fullReasoning } : {}),
                    ...(finalRichParts.length ? { richParts: [...finalRichParts] } : {}),
                    ...(finalSources.length ? { sources: [...finalSources] } : {}),
                });
            }

            // If backend assigned a different session ID, migrate AFTER storing final content
            // so messages stay accessible under the new ID and the loading effect finds them.
            if (backendAssignedSessionId) {
                // Update streaming ref BEFORE migration so session-switch effect
                // doesn't call resetRichParts() while word-queue is still draining.
                streamingForSessionRef.current = backendAssignedSessionId;
                loadedForSessionRef.current = backendAssignedSessionId;
                migrateSession(sessionId, backendAssignedSessionId);
            } else {
                // No migration — mark this session as loaded so the message-loading
                // effect doesn't overwrite our live messages on React Query refetches.
                loadedForSessionRef.current = sessionId;
            }

            clearInterval(flushIntervalId);
            window.removeEventListener('beforeunload', handleBeforeUnload);
            clearRequestTimeout();
            setIsLoading(false);
            setIsStreaming(false);
            setStreamingMessageId(undefined);
            streamingForSessionRef.current = null;
            // Skills persist per session — do not reset after send
        } catch (err) {
            if (flushIntervalId) clearInterval(flushIntervalId);
            if (handleBeforeUnload) window.removeEventListener('beforeunload', handleBeforeUnload);
            clearRequestTimeout();

            if (err instanceof Error && err.name === 'AbortError') {
                if (!error) {
                    setError('Request was cancelled.');
                }
            } else {
                setError(err instanceof Error ? err.message : 'Failed to send message');
            }

            // If we accumulated partial content, persist it before clearing
            if (fullContent && sessionId) {
                updateLastAssistantInSession(sessionId, {
                    content: fullContent,
                    ...(fullReasoning ? { reasoning: fullReasoning } : {}),
                });
            }

            setMessages((prev) => prev.filter((m) => m.id !== assistantMessageId));
            // Mark session as loaded so message-loading effect doesn't overwrite
            if (sessionId) loadedForSessionRef.current = sessionId;
            resetRichParts(); // Clear stale streaming state from failed stream
            setIsLoading(false);
            setIsStreaming(false);
            setStreamingMessageId(undefined);
            streamingForSessionRef.current = null;
            setActiveToolCalls([]);
        }
    }, [
        messages,
        isLoading,
        error,
        clearRequestTimeout,
        currentSessionId,
        createSession,
        setCurrentSession,
        addMessageToSession,
        updateLastAssistantInSession,
        getSession,
        selectedModel,
        thinkingSupported,
        selectedSkills,
        getActiveSources,
        resetRichParts,
        richPartHandlers,
        finalizeRichParts,
        migrateSession,
    ]);

    // Deep Work integration (extracted to hook for maintainability)
    const {
        deepWorkStatus,
        isDeepWorkActive,
        isDeepWorkForThisChat,
        currentActivity,
        resultMessage: deepWorkResultMessage,
    } = useDeepWorkChat(currentSessionId);

    // Append deep work result message to local messages list
    const deepWorkResultAppendedRef = useRef<string | null>(null);
    useEffect(() => {
        if (!deepWorkResultMessage) return;
        const dedupeKey = deepWorkResultMessage.content.slice(0, 80);
        if (deepWorkResultAppendedRef.current === dedupeKey) return;
        deepWorkResultAppendedRef.current = dedupeKey;
        setMessages((prev) => {
            if (prev.some((m) => m.role === 'assistant' && m.content === deepWorkResultMessage.content)) return prev;
            return [...prev, { id: generateMessageId(), role: 'assistant', content: deepWorkResultMessage.content }];
        });
    }, [deepWorkResultMessage]);

    // Consume pending input from ProjectView and auto-send (project chats only).
    const pendingConsumedRef = useRef(false);

    useEffect(() => {
        if (!projectId || pendingConsumedRef.current) return;
        pendingConsumedRef.current = true;
        const pending = consumePendingInput();
        if (pending) {
            sendMessage(pending);
        }
    }, [projectId, consumePendingInput, sendMessage]);

    const handleSubmit = useCallback(() => {
        if (!input.trim() || isLoading || isStreaming) return;
        if (error) setError(null); // auto-dismiss previous error on new submission
        const text = input;
        setInput('');

        if (deepWorkMode) {
            handleDeepWorkSubmit(text);
        } else {
            sendMessage(text);
        }
    }, [input, isLoading, isStreaming, deepWorkMode, sendMessage, handleDeepWorkSubmit]);

    const handleRetry = useCallback(() => {
        setError(null);
    }, []);

    const handleDismissError = useCallback(() => {
        setError(null);
    }, []);

    const handleStop = useCallback(() => {
        abortControllerRef.current?.abort();
        clearRequestTimeout();
        setIsLoading(false);
        setIsStreaming(false);
        setStreamingMessageId(undefined);
        setActiveToolCalls([]);
        setStreamStale(false);
    }, [clearRequestTimeout]);

    // Deep work suggestion banner handlers
    const handleAcceptDeepWork = useCallback(() => {
        setDeepWorkSuggestion(null);
        // Abort the current (shallow) stream
        abortControllerRef.current?.abort();
        clearRequestTimeout();
        setIsLoading(false);
        setIsStreaming(false);
        setStreamingMessageId(undefined);
        setActiveToolCalls([]);
        setStreamStale(false);
        // Start deep work with the same query
        const msg = lastUserMessageRef.current;
        if (msg) {
            handleDeepWorkSubmit(msg);
        }
    }, [clearRequestTimeout, handleDeepWorkSubmit]);

    const handleDismissDeepWork = useCallback(() => {
        setDeepWorkSuggestion(null);
    }, []);

    // Workflow execution handlers
    const [isWorkflowExecuting, setIsWorkflowExecuting] = useState(false);
    const [workflowDismissed, setWorkflowDismissed] = useState(false);

    const handleWorkflowExecute = useCallback(async (workflowId: string, inputs: Record<string, any>) => {
        const agentId = localSession?.agentId;
        const sessionId = currentSessionId;
        if (!agentId || !sessionId) return;

        setIsWorkflowExecuting(true);
        try {
            const response = await executeWorkflow(agentId, sessionId, workflowId, inputs);
            const reader = response.body?.getReader();
            if (!reader) throw new Error('No response body');

            // Parse the execution SSE stream and route events to the workflow handlers
            await parseWorkflowSSE(reader, {
                onContentDelta: () => {},
                onReasoningDelta: () => {},
                onThinkingDelta: () => {},
                onToolCallStart: () => {},
                onToolCallProgress: () => {},
                onToolCallComplete: () => {},
                onSource: () => {},
                onInfoMessage: () => {},
                onPart: () => {},
                onWorkflowProgress: (data) => {
                    richPartHandlers.onWorkflowProgress?.(data);
                },
                onWorkflowComplete: (data) => {
                    richPartHandlers.onWorkflowComplete?.(data);
                },
                onError: () => {},
                onDone: () => {},
            });
        } catch (err: any) {
            console.error('Workflow execution failed:', err);
        } finally {
            setIsWorkflowExecuting(false);
        }
    }, [localSession?.agentId, currentSessionId, richPartHandlers]);

    const handleWorkflowCancel = useCallback(() => {
        setWorkflowDismissed(true);
    }, []);

    // Reset workflow dismissed state when a new preview arrives
    useEffect(() => {
        if (workflowPreview) {
            setWorkflowDismissed(false);
        }
    }, [workflowPreview]);

    // Sync richParts into the streaming message so ChatMessageList can access them
    useEffect(() => {
        if (!streamingMessageId || !streamingRichParts.length) return;
        setMessages((prev) =>
            prev.map((m) =>
                m.id === streamingMessageId
                    ? { ...m, richParts: streamingRichParts }
                    : m
            )
        );
    }, [streamingMessageId, streamingRichParts]);

    // Stale stream detection: warn if no data received for 30s while streaming
    // (suspended during tool execution and reasoning since both can take >30s)
    useEffect(() => {
        if (!isStreaming) {
            setStreamStale(false);
            return;
        }
        const interval = setInterval(() => {
            const elapsed = Date.now() - lastEventTimeRef.current;
            const hasRunningTools = activeToolCalls.some(tc => tc.status === 'running');
            const hasActiveThinking = streamingRichParts.some(
                p => p.type === 'thinking' && (p as any).isThinking
            );
            setStreamStale(elapsed > 30000 && !hasRunningTools && !hasActiveThinking);
        }, 5000);
        return () => clearInterval(interval);
    }, [isStreaming, activeToolCalls, streamingRichParts]);

    useEffect(() => {
        if (viewportRef.current) {
            viewportRef.current.scrollTop = viewportRef.current.scrollHeight;
        }
    }, [messages]);

    const hasMessages = messages.length > 0;


    // Source control popovers passed as ReactNode to input components
    const { webSearchPopover, knowledgePopover } = buildSelectionPopovers(activeSources, {
        onToggleWebSearch: handleToggleWebSearch,
        onAddSite: handleAddSite,
        onRemoveSite: handleRemoveSite,
        onToggleKnowledgeBase: handleToggleKnowledgeBase,
    });

    const inputProps = {
        value: input,
        onChange: handleInputChange,
        onSubmit: handleSubmit,
        isLoading,
        deepWorkMode,
        onDeepWorkToggle: toggleDeepWork,
        thinkingEnabled,
        onThinkingToggle: toggleThinking,
        thinkingSupported,
        inputRef,
        selectedSkills,
        onRemoveSkill: handleRemoveSkill,
        sessionId: currentSessionId,
        activeConnectorIds,
        onToggleConnector: handleToggleConnector,
        skillPickerOpen,
        onSkillPickerOpenChange: setSkillPickerOpen,
        slashQuery,
        onSkillSelect: handleSkillSelect,
        webSearchPopover,
        knowledgePopover,
    };

    return (
        <ChatShell
            projectId={projectId}
            projectName={project?.name}
            agentInfo={agentInfo}
            hasMessages={hasMessages}
            onBackToProject={() => setCurrentSession(null)}
            onRename={() => {
                if (!currentSessionId) return;
                const title = prompt('Rename chat', currentSession?.title || 'New Chat');
                if (title?.trim()) updateSessionTitle(currentSessionId, title.trim());
            }}
            onDelete={() => {
                if (!currentSessionId) return;
                if (confirm('Delete this chat?')) {
                    deleteSession(currentSessionId);
                    setMessages([]);
                }
            }}
            welcome={<WelcomeComposer {...inputProps} />}
            messageArea={
                <MessageList
                    scrollRef={scrollRef}
                    viewportRef={viewportRef}
                    messages={messages}
                    streamingMessageId={streamingMessageId}
                    streamingRichParts={streamingRichParts}
                    streamingToolCallMeta={streamingToolCallMeta}
                    richSources={richSources}
                    onOpenModal={handleOpenModal}
                    activeToolCalls={activeToolCalls}
                    workflowPreview={workflowPreview}
                    workflowProgress={workflowProgress}
                    workflowResult={workflowResult}
                    workflowDismissed={workflowDismissed}
                    isWorkflowExecuting={isWorkflowExecuting}
                    onWorkflowExecute={handleWorkflowExecute}
                    onWorkflowCancel={handleWorkflowCancel}
                    isDeepWorkForThisChat={isDeepWorkForThisChat}
                    isLoading={isLoading}
                    isStreaming={isStreaming}
                    streamStale={streamStale}
                    error={error}
                    onRetry={handleRetry}
                    onDismissError={handleDismissError}
                />
            }
            composer={
                <Composer
                    {...inputProps}
                    isStreaming={isStreaming}
                    onStop={handleStop}
                    deepWorkSuggestion={deepWorkSuggestion}
                    onAcceptDeepWork={handleAcceptDeepWork}
                    onDismissDeepWork={handleDismissDeepWork}
                    isDeepWorkExecuting={isDeepWorkActive && isDeepWorkForThisChat}
                    deepWorkActivity={isDeepWorkForThisChat ? currentActivity : null}
                />
            }
            rightPanel={
                <RightPanel
                    files={allFiles}
                    fileMeta={fileMeta}
                    onFileSelect={handleFileSelect}
                    sources={allSources}
                />
            }
            overlay={
                /* Artifact modal overlay */
                artifactModal?.open && (
                    <ArtifactModal
                        open={artifactModal.open}
                        onOpenChange={(open) => setArtifactModal(open ? artifactModal : null)}
                        filename={artifactModal.filename}
                        content={artifactModal.content}
                        fileType={artifactModal.fileType}
                        downloadUrl={artifactModal.downloadUrl}
                    />
                )
            }
        />
    );
}
