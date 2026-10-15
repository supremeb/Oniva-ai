'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
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
    const client = useMemo(() => clientProp ?? new EngineConversationClient(), [clientProp]);
    const [conversationId, setConversationId] = useState<string | null>(initialConversationId);
    const conv = useEngineConversation(conversationId, { client, watcher: watcherOptions });
    const { view, connection, pending, error, submit, retry, abort, decide } = conv;
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

    // Project for a new conversation: the page's project, else the user's first active one.
    const projects = useProjectsStore((s) => s.projects);
    const loadProjects = useProjectsStore((s) => s.loadProjects);
    useEffect(() => {
        if (!projectId && projects.length === 0) void loadProjects();
    }, [projectId, projects.length, loadProjects]);
    const resolvedProjectId = projectId ?? projects.find((p) => !p.is_archived)?.id ?? null;

    const adapt = useMemo(() => createViewAdapter(), []);
    const messages = useMemo(() => (view ? adapt(view) : []), [adapt, view]);

    // Approve/deny go through the backend route. The card keeps itself disabled
    // until the approvals document reflects the decision; a failure surfaces inline.
    const approvalHandlers = useMemo(
        () => ({
            onApprove: (toolTaskId: string) => decide(toolTaskId, 'approve').catch((e) => { throw toDecisionError(e); }),
            onReject: (toolTaskId: string) => decide(toolTaskId, 'deny').catch((e) => { throw toDecisionError(e); }),
        }),
        [decide]
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

    const send = useCallback(
        async (mode: EngineSendMode) => {
            const text = input.trim();
            if (!text || creating) return;
            if (!conversationId) {
                if (!resolvedProjectId) {
                    setCreateError('Create a project first to start a conversation.');
                    return;
                }
                setCreating(true);
                setCreateError(null);
                try {
                    const created = await client.create(resolvedProjectId, toSelections(sources));
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
                return;
            }
            useEngineChatPrefs.getState().clearDraft(draftKey);
            setInputState('');
            const whenBusy = running ? (mode === 'steer' ? 'steer' : 'followUp') : undefined;
            await doSubmit(text, whenBusy);
        },
        [input, creating, conversationId, resolvedProjectId, client, sources, draftKey, running, doSubmit]
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
    const hasMessages = messages.length > 0 || bubbles.length > 0 || sending.length > 0 || creating;

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
            chips={<QueuedChips inbox={view?.inbox ?? null} sending={sending} />}
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
                    onOpenModal={(data) => setArtifactModal({ ...data, open: true })}
                />
            }
            composer={
                <div className="px-6 pb-5 pt-3">
                    {createError && <ChatError message={createError} onDismiss={() => setCreateError(null)} className="mb-3 max-w-[680px] mx-auto" />}
                    <ConnectionBanner connection={connection} error={error} stalledAfterSeconds={Math.round((watcherOptions?.stalledAfterMs ?? 45_000) / 1000)} />
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
