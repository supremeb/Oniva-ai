'use client';

import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { QueryClientContext } from '@tanstack/react-query';
import { updateEngineConversation } from '@/lib/api/conversation-union';
import { GenerativeO } from '@/components/ui/GenerativeO';
import { ChatError } from './shared';
import { ArtifactModal } from './shared/ArtifactModal';
import type { ArtifactModalData } from './shared/data/FilePreview';
import { RightPanel } from './RightPanel';
import { ChatShell } from './ChatShell';
import { EngineComposer, type EngineRunState, type EngineSendMode } from './Composer';
import { EngineMessageList } from './EngineMessageList';
import { QueuedChips, type SendingChip } from './QueuedChips';
import { ConnectionBanner } from './ConnectionBanner';
import { buildSelectionPopovers } from './ChatSelections';
import { EngineSkillConnectorPickers } from './EngineSkillConnectorPickers';
import { SkillTagRow } from './inputs/shared';
import type { Skill } from '@/lib/api/skills';
import { CostBadge, runForTurn } from './CostBadge';
import { isRunActive } from '@/lib/engine/run';
import { useConversationCost, type ConversationCost } from '@/lib/engine/cost';
import {
    createViewAdapter,
    isCompactionRunning,
    toDecisionError,
    EngineConversationClient,
    type EngineMessage,
    useEngineConversation,
    type PendingEntry,
    type WatcherOptions,
} from '@/lib/engine';
import { useProjectsStore } from '@/lib/stores/projects';
import { useDeepWorkIntake } from '@/hooks/useDeepWorkIntake';
import { useDeepWorkStore } from '@/lib/stores/deep-work';
import { DeepWorkCard } from '@/components/deep-work/DeepWorkCard';
import { isEngineRuntimeSession, useEngineSessionStore } from '@/components/deep-work/engineSession';
import { getEngineFullState } from '@/lib/engine/deepWorkApi';
import { DeepWorkToggle } from './inputs/WelcomeInput';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import { useEngineChatPrefs, NEW_DRAFT_KEY } from '@/lib/stores/engine-chat-prefs';

export interface EngineChatProps {
    projectId?: string;
    /** Conversation to rejoin (`?c=` in the URL). */
    initialConversationId?: string | null;
    /** Injected in tests. */
    client?: EngineConversationClient;
    watcherOptions?: WatcherOptions;
    /** Injected in tests. */
    costFetcher?: (conversationId: string) => Promise<ConversationCost>;
}

interface Sources {
    webSearchEnabled: boolean;
    specificSites: string[];
    activeKnowledgeBaseIds: string[];
    skills: Skill[];
    connectorIds: string[];
}
const NO_SOURCES: Sources = { webSearchEnabled: false, specificSites: [], activeKnowledgeBaseIds: [], skills: [], connectorIds: [] };

const toSelections = (s: Sources) => ({
    web_search: s.webSearchEnabled,
    specific_sites: s.specificSites,
    knowledge_base_ids: s.activeKnowledgeBaseIds,
    skill_ids: s.skills.map((k) => k.id),
    connector_ids: s.connectorIds,
});

/** Put the conversation id in the URL so a refresh rejoins it (R11). */
function writeConversationToUrl(id: string) {
    try {
        const url = new URL(window.location.href);
        url.searchParams.set('c', id);
        window.history.replaceState(window.history.state, '', url);
    } catch {
        /* non-browser */
    }
}

/**
 * Chat on the engine. One state owner: the server view from `useEngineConversation`
 * plus optimistic pending entries the adapter does not merge (KTD12, R19).
 */
export function EngineChat({ projectId, initialConversationId = null, client: clientProp, watcherOptions, costFetcher }: EngineChatProps) {
    const router = useRouter();
    // Optional: the sidebar list refreshes when present (a Deep Work start creates a chat the list must show).
    const queryClient = useContext(QueryClientContext);
    const client = useMemo(() => clientProp ?? new EngineConversationClient(), [clientProp]);
    const [conversationId, setConversationId] = useState<string | null>(initialConversationId);
    const conv = useEngineConversation(conversationId, { client, watcher: watcherOptions });
    const { view, connection, pending, error, submit, retry, abort, decide, answerQuestion, cancelSubmission } = conv;
    const { cost, active: costActive, liveTokens, refresh: refreshCost } = useConversationCost(conversationId, view, costFetcher);

    const draftKey = conversationId ?? NEW_DRAFT_KEY;
    const [input, setInputState] = useState(() => useEngineChatPrefs.getState().drafts[draftKey] ?? '');
    const setInput = useCallback(
        (text: string) => {
            setInputState(text);
            useEngineChatPrefs.getState().setDraft(draftKey, text);
        },
        [draftKey]
    );

    const [sources, setSources] = useState<Sources>(NO_SOURCES);
    const [stopping, setStopping] = useState(false);
    const [creating, setCreating] = useState(false);
    const [createError, setCreateError] = useState<string | null>(null);
    const [accepted, setAccepted] = useState<ReadonlySet<string>>(new Set());
    const [artifactModal, setArtifactModal] = useState<(ArtifactModalData & { open: boolean }) | null>(null);
    const firstText = useRef<string | null>(null);
    const scrollRef = useRef<HTMLDivElement>(null);
    const viewportRef = useRef<HTMLDivElement>(null);

    // Project for a new conversation: the page's project, else the most recent project
    // the user owns, creating a private "Personal" one when they own none (KTD1). The
    // resolution is shared and cached so a send and the Deep Work intake agree on it.
    const resolveDefaultProject = useProjectsStore((s) => s.resolveDefaultProject);
    const projectResolution = useRef<Promise<string> | null>(null);
    const ensureProject = useCallback(async (): Promise<string> => {
        if (projectId) return projectId;
        if (!projectResolution.current) {
            projectResolution.current = resolveDefaultProject().then((p) => p.id);
            projectResolution.current.catch(() => {
                projectResolution.current = null;
            });
        }
        return projectResolution.current;
    }, [projectId, resolveDefaultProject]);

    // Deep Work: offered only when the tenant's Deep Work runs on the engine (the same entry
    // point the legacy chat has: a toggle in the input, the next send starts the intake).
    const { mode: deepWorkRuntime, startIntake } = useDeepWorkIntake(projectId);
    const [deepWorkOn, setDeepWorkOn] = useState(false);
    const [deepWorkQuery, setDeepWorkQuery] = useState<string | null>(null);
    const dwChatKey = useRef(`engine-dw-${Math.random().toString(36).slice(2)}`);
    const dwIntakeActive = useDeepWorkStore((s) => s.status !== 'idle');
    const dwEngineSession = useEngineSessionStore((s) => s.session);
    const deepWorkVisible = deepWorkRuntime === 'engine' && (dwIntakeActive || dwEngineSession !== null);
    // Rejoin: a refresh or a second tab on `?c=` finds the run this chat started (by chat id) and shows its
    // card from the watch view. Nothing is started or submitted; a chat without a run answers 404.
    useEffect(() => {
        if (deepWorkRuntime !== 'engine' || !initialConversationId) return;
        let cancelled = false;
        void getEngineFullState(initialConversationId)
            .then((fs) => {
                if (cancelled || !isEngineRuntimeSession(fs) || useEngineSessionStore.getState().session) return;
                useEngineSessionStore.getState().setSession({
                    sessionId: fs.session_id,
                    conversationId: fs.conversation_id,
                    chatId: initialConversationId,
                    status: fs.status,
                    error: fs.error ?? null,
                    planTitle: fs.plan?.title ?? null,
                });
            })
            .catch(() => {});
        return () => {
            cancelled = true;
        };
    }, [deepWorkRuntime, initialConversationId]);
    // Leaving the chat drops the card so it never shows up in another conversation.
    useEffect(
        () => () => {
            useEngineSessionStore.getState().setSession(null);
            useDeepWorkStore.getState().reset();
        },
        []
    );

    const adapt = useMemo(() => createViewAdapter(), []);
    const messages = useMemo(() => (view ? adapt(view) : []), [adapt, view]);

    // Approve/deny go through the backend route. The card keeps itself disabled
    // until the approvals document reflects the decision; a failure surfaces inline.
    const approvalHandlers = useMemo(
        () => ({
            onApprove: (toolTaskId: string) => decide(toolTaskId, 'approve').catch((e) => { throw toDecisionError(e); }),
            onReject: (toolTaskId: string) => decide(toolTaskId, 'deny').catch((e) => { throw toDecisionError(e); }),
            // Same lifecycle for questions: the card stays disabled until the questions document changes.
            onQuestionAnswer: (toolTaskId: string, a: { optionIds: string[]; otherValue?: string }) =>
                answerQuestion(toolTaskId, { optionIds: a.optionIds, ...(a.otherValue !== undefined ? { text: a.otherValue } : {}) }).catch((e) => {
                    throw toDecisionError(e);
                }),
        }),
        [decide, answerQuestion]
    );

    // Per-turn cost under the last assistant message of each run.
    const liveRunId = view?.live.runId ?? null;
    const lastOfRun = useMemo(() => {
        const order: string[] = [];
        const last = new Map<string, string>();
        for (const m of messages) {
            if (m.role !== 'assistant' || !m.runId) continue;
            if (!last.has(m.runId)) order.push(m.runId);
            last.set(m.runId, m.id);
        }
        return { order, last };
    }, [messages]);
    const renderMessageFooter = useCallback(
        (message: { id: string }) => {
            const runId = (message as EngineMessage).runId;
            if (!runId || lastOfRun.last.get(runId) !== message.id || runId === liveRunId) return null;
            const runs = cost?.runs ?? [];
            // Match the ledger by run id; if the ids do not line up, fall back to start order.
            const byId = runs.find((r) => r.runId === runId);
            const run = byId ?? (runs.some((r) => lastOfRun.order.includes(r.runId)) ? null : runForTurn(cost ?? null, lastOfRun.order.indexOf(runId)));
            return run ? <CostBadge variant="turn" run={run} /> : null;
        },
        [lastOfRun, liveRunId, cost]
    );

    const running = isRunActive(view);
    const hasPendingApproval = view?.approvals.items.some((a) => a.status === 'pending') ?? false;
    const awaitingApproval = running && (view?.approvals.items.some((a) => a.status === 'pending') ?? false);
    const runState: EngineRunState = awaitingApproval ? 'awaiting_approval' : running ? 'running' : 'idle';
    useEffect(() => {
        if (!running) setStopping(false);
    }, [running]);

    // Optimistic entries: idle sends and failures show as bubbles; busy sends show as chips until acknowledged.
    const bubbles = pending.filter((p: PendingEntry) => p.status === 'failed' || !p.whenBusy);
    const sending: SendingChip[] = pending
        .filter((p) => p.status === 'sending' && p.whenBusy && p.whenBusy !== 'reject' && !accepted.has(p.requestId))
        .map((p) => ({ requestId: p.requestId, mode: p.whenBusy as 'steer' | 'followUp', text: p.text }));

    const markAccepted = useCallback((id: string) => setAccepted((s) => new Set(s).add(id)), []);

    const doSubmit = useCallback(
        async (text: string, whenBusy?: 'steer' | 'followUp') => {
            try {
                const id = await submit({ text, ...(whenBusy ? { whenBusy } : {}) });
                if (whenBusy) markAccepted(id);
            } catch {
                /* the pending entry is marked failed and offers Retry */
            }
        },
        [submit, markAccepted]
    );

    // First send creates the conversation; submit once its watcher exists.
    useEffect(() => {
        if (conversationId && firstText.current !== null) {
            const text = firstText.current;
            firstText.current = null;
            void doSubmit(text);
        }
    }, [conversationId, doSubmit]);

    // Create the conversation; the first-send effect above submits `text` once the watcher exists.
    const startConversation = useCallback(
        async (text: string) => {
            setCreating(true);
            setCreateError(null);
            let targetProjectId = projectId;
            if (!targetProjectId) {
                try {
                    targetProjectId = await ensureProject();
                } catch {
                    setCreateError('Pick or create a project to start a conversation.');
                    setCreating(false);
                    return;
                }
            }
            try {
                const created = await client.create(targetProjectId, toSelections(sources));
                useEngineChatPrefs.getState().clearDraft(NEW_DRAFT_KEY);
                setInputState('');
                firstText.current = text;
                writeConversationToUrl(created.conversationId);
                setConversationId(created.conversationId);
            } catch (e) {
                setCreateError(e instanceof Error ? e.message : 'Could not start the conversation.');
            } finally {
                setCreating(false);
            }
        },
        [projectId, ensureProject, client, sources]
    );

    // Text typed in ProjectView before starting the chat: send it as the first message, once.
    // The ref survives the strict-mode effect replay and the store read clears the value.
    const consumePendingInput = useChatSessionsStore((s) => s.consumePendingInput);
    const pendingHandled = useRef(false);
    useEffect(() => {
        if (pendingHandled.current || !projectId || initialConversationId) return;
        pendingHandled.current = true;
        const pendingText = consumePendingInput()?.trim();
        if (pendingText) void startConversation(pendingText);
    }, [projectId, initialConversationId, consumePendingInput, startConversation]);

    const send = useCallback(
        async (mode: EngineSendMode) => {
            const text = input.trim();
            if (!text || creating) return;
            if (deepWorkRuntime === 'engine' && deepWorkOn && !running) {
                useEngineChatPrefs.getState().clearDraft(draftKey);
                setInputState('');
                setDeepWorkOn(false);
                setDeepWorkQuery(text);
                let targetProjectId = projectId;
                if (!targetProjectId) {
                    try {
                        targetProjectId = await ensureProject();
                    } catch {
                        setCreateError('Pick or create a project to use Deep Work.');
                        return;
                    }
                }
                // A Deep Work started from a new chat gets a real chat conversation first: it is what the sidebar lists,
                // what `?c=` rejoins after navigating away, and the chat id the run is bound to.
                let chatId = conversationId;
                if (!chatId) {
                    try {
                        const created = await client.create(targetProjectId, toSelections(sources));
                        chatId = created.conversationId;
                        writeConversationToUrl(chatId);
                        setConversationId(chatId);
                        const title = text.slice(0, 80);
                        // Best effort: a failed rename leaves "New conversation"; it never undoes the chat.
                        void Promise.resolve()
                            .then(() => updateEngineConversation(created.conversationId, { title }))
                            .catch(() => {})
                            .finally(() => void queryClient?.invalidateQueries({ queryKey: ['chat-sessions'] }));
                    } catch {
                        chatId = dwChatKey.current;
                    }
                }
                void startIntake(text, chatId, sources.activeKnowledgeBaseIds, sources.connectorIds, targetProjectId);
                return;
            }
            if (!conversationId) {
                await startConversation(text);
                return;
            }
            useEngineChatPrefs.getState().clearDraft(draftKey);
            setInputState('');
            const whenBusy = running ? (mode === 'steer' ? 'steer' : 'followUp') : undefined;
            await doSubmit(text, whenBusy);
        },
        [input, creating, conversationId, startConversation, draftKey, running, doSubmit, deepWorkRuntime, deepWorkOn, startIntake, projectId, ensureProject, client, sources, queryClient]
    );

    const handleStop = useCallback(() => {
        setStopping(true);
        abort().catch(() => setStopping(false));
    }, [abort]);

    const handleRetry = useCallback(
        (requestId: string) => {
            retry(requestId)
                .then(() => markAccepted(requestId))
                .catch(() => {});
        },
        [retry, markAccepted]
    );

    // Selections changes apply to the live conversation through the config route.
    const updateSources = useCallback(
        (next: Sources) => {
            setSources(next);
            if (conversationId) void client.updateSelections(conversationId, toSelections(next)).catch(() => {});
        },
        [conversationId, client]
    );
    const { webSearchPopover, knowledgePopover } = buildSelectionPopovers(sources, {
        onToggleWebSearch: () => updateSources({ ...sources, webSearchEnabled: !sources.webSearchEnabled }),
        onAddSite: (url) =>
            updateSources({ ...sources, specificSites: sources.specificSites.includes(url) ? sources.specificSites : [...sources.specificSites, url] }),
        onRemoveSite: (url) => updateSources({ ...sources, specificSites: sources.specificSites.filter((s) => s !== url) }),
        onToggleKnowledgeBase: (id) =>
            updateSources({
                ...sources,
                activeKnowledgeBaseIds: sources.activeKnowledgeBaseIds.includes(id)
                    ? sources.activeKnowledgeBaseIds.filter((k) => k !== id)
                    : [...sources.activeKnowledgeBaseIds, id],
            }),
    });

    useEffect(() => {
        if (viewportRef.current) viewportRef.current.scrollTop = viewportRef.current.scrollHeight;
    }, [messages, pending]);

    const panelSources = useMemo(() => messages.flatMap((m) => m.sources ?? []), [messages]);
    const closed = connection === 'closed' && error !== null;
    const hasMessages = messages.length > 0 || bubbles.length > 0 || sending.length > 0 || creating || deepWorkVisible;

    const composer = (
        <EngineComposer
            value={input}
            onChange={setInput}
            runState={runState}
            stopping={stopping}
            disabled={closed}
            onSend={send}
            onStop={handleStop}
            toolbar={
                <>
                    {webSearchPopover}
                    {knowledgePopover}
                    {deepWorkRuntime === 'engine' && <DeepWorkToggle active={deepWorkOn} onToggle={() => setDeepWorkOn((v) => !v)} />}
                    <EngineSkillConnectorPickers
                        conversationId={conversationId}
                        skills={sources.skills}
                        connectorIds={sources.connectorIds}
                        onSelectSkill={(skill) =>
                            updateSources({ ...sources, skills: sources.skills.some((k) => k.id === skill.id) ? sources.skills : [...sources.skills, skill] })
                        }
                        onToggleConnector={(id) =>
                            updateSources({
                                ...sources,
                                connectorIds: sources.connectorIds.includes(id) ? sources.connectorIds.filter((c) => c !== id) : [...sources.connectorIds, id],
                            })
                        }
                    />
                </>
            }
            header={
                sources.skills.length > 0 ? (
                    <div className="pt-2.5">
                        <SkillTagRow skills={sources.skills} onRemove={(id) => updateSources({ ...sources, skills: sources.skills.filter((k) => k.id !== id) })} />
                    </div>
                ) : null
            }
            chips={<QueuedChips inbox={view?.inbox ?? null} sending={sending} onCancel={cancelSubmission} />}
        />
    );

    return (
        <ChatShell
            projectId={projectId}
            agentInfo={null}
            hasMessages={hasMessages}
            onBackToProject={() => projectId && router.push(`/chat/projects/${projectId}`)}
            welcome={
                <div className="flex-1 flex flex-col items-center justify-center px-6">
                    <div className="flex items-center gap-4 mb-10">
                        <GenerativeO size={40} speed={1} animating={false} />
                        <h1 className="text-[28px] font-semibold text-neutral-900 tracking-tight">How can I help?</h1>
                    </div>
                    {createError && <ChatError message={createError} onDismiss={() => setCreateError(null)} className="mb-4 w-full max-w-[680px]" />}
                    {conversationId && <ConnectionBanner connection={connection} error={error} stalledAfterSeconds={Math.round((watcherOptions?.stalledAfterMs ?? 45_000) / 1000)} />}
                    {composer}
                </div>
            }
            messageArea={
                <EngineMessageList
                    scrollRef={scrollRef}
                    viewportRef={viewportRef}
                    messages={messages}
                    pending={bubbles}
                    running={running}
                    compacting={isCompactionRunning(view)}
                    onRetry={handleRetry}
                    approvalHandlers={approvalHandlers}
                    renderMessageFooter={renderMessageFooter}
                    footer={
                        deepWorkVisible ? (
                            <>
                                {deepWorkQuery && (
                                    <div className="mt-6 flex justify-end" data-testid="deep-work-query">
                                        <div className="max-w-[85%] rounded-2xl bg-neutral-100 px-4 py-2.5 text-[14px] text-neutral-900 whitespace-pre-wrap">{deepWorkQuery}</div>
                                    </div>
                                )}
                                <DeepWorkCard />
                            </>
                        ) : null
                    }
                    onOpenModal={(data) => setArtifactModal({ ...data, open: true })}
                />
            }
            composer={
                <div className="px-6 pb-5 pt-3">
                    {createError && <ChatError message={createError} onDismiss={() => setCreateError(null)} className="mb-3 max-w-[680px] mx-auto" />}
                    <ConnectionBanner connection={connection} error={error} stalledAfterSeconds={Math.round((watcherOptions?.stalledAfterMs ?? 45_000) / 1000)} />
                    {hasPendingApproval && (
                        <div
                            data-testid="approval-banner"
                            role="status"
                            className="mb-3 max-w-[680px] mx-auto flex items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[13px] text-neutral-800"
                        >
                            <span>Awaiting approval before the agent continues.</span>
                            <Link href="/inbox" className="font-medium underline underline-offset-2">
                                Open inbox
                            </Link>
                        </div>
                    )}
                    {composer}
                </div>
            }
            headerExtra={
                conversationId ? (
                    <span className="self-center mr-1 text-neutral-500">
                        <CostBadge variant="total" cost={cost ?? null} active={costActive} liveTokens={liveTokens} onRefresh={() => void refreshCost()} />
                    </span>
                ) : null
            }
            rightPanel={<RightPanel files={[]} sources={panelSources} />}
            overlay={
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
