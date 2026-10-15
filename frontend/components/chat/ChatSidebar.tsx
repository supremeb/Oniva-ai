'use client';

import { memo, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
    PencilSimpleLine,
    Robot,
    DotsThree,
    FolderSimple,
    Plus,
    ChatCircle,
    Gear,
    PushPin,
    PencilSimple,
    Trash,
    Check,
    X,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import { useProjectsStore } from '@/lib/stores/projects';
import { useAgents } from '@/hooks/useAgents';
import { BUILTIN_AGENT_UUIDS } from '@/lib/builtin-agents';
import { AgentBrowserModal } from './AgentBrowserModal';
import { useChatSessionsList, useUpdateChatSession, useDeleteChatSession } from '@/hooks/useChatSessions';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useEngineChatMode } from '@/lib/hooks/useFeature';
import { useConversationUnion } from '@/hooks/useConversationUnion';
import { useEngineNav } from '@/lib/stores/engine-nav';
import type { ConversationListItem } from '@/lib/api/conversation-union';
import type { ChatSession as BackendChatSession } from '@/lib/types/chat-panels';

interface ChatSidebarProps {
    className?: string;
}

// Inline rename input for a session
const RenameInput = memo(function RenameInput({
    initialValue,
    onSave,
    onCancel,
}: {
    initialValue: string;
    onSave: (value: string) => void;
    onCancel: () => void;
}) {
    const [value, setValue] = useState(initialValue);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
    }, []);

    const handleSave = useCallback(() => {
        const trimmed = value.trim();
        if (trimmed && trimmed !== initialValue) {
            onSave(trimmed);
        } else {
            onCancel();
        }
    }, [value, initialValue, onSave, onCancel]);

    return (
        <div className="flex items-center gap-1 w-full px-2 py-1">
            <input
                ref={inputRef}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSave();
                    if (e.key === 'Escape') onCancel();
                }}
                onBlur={handleSave}
                className="flex-1 text-sm bg-background border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/30 min-w-0"
            />
        </div>
    );
});

// Delete confirmation inline
const DeleteConfirmation = memo(function DeleteConfirmation({
    onConfirm,
    onCancel,
}: {
    onConfirm: () => void;
    onCancel: () => void;
}) {
    return (
        <div className="flex items-center gap-1 w-full px-2 py-1.5 text-xs text-destructive">
            <span className="flex-1 truncate">Delete this chat?</span>
            <button
                onClick={(e) => { e.stopPropagation(); onConfirm(); }}
                className="h-5 w-5 flex items-center justify-center rounded hover:bg-destructive/10 transition-colors"
            >
                <Check className="h-3.5 w-3.5" />
            </button>
            <button
                onClick={(e) => { e.stopPropagation(); onCancel(); }}
                className="h-5 w-5 flex items-center justify-center rounded hover:bg-muted transition-colors text-muted-foreground"
            >
                <X className="h-3.5 w-3.5" />
            </button>
        </div>
    );
});

// Session options dropdown shared between backend and local session items
const SessionOptionsMenu = memo(function SessionOptionsMenu({
    onRename,
    onDelete,
    onTogglePin,
    isPinned,
    showPin = true,
}: {
    onRename: () => void;
    onDelete: () => void;
    onTogglePin?: () => void;
    isPinned?: boolean;
    showPin?: boolean;
}) {
    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <button
                    onClick={(e) => e.stopPropagation()}
                    className="h-5 w-5 flex items-center justify-center rounded hover:bg-accent transition-colors text-muted-foreground opacity-0 group-hover:opacity-100 shrink-0"
                >
                    <DotsThree className="h-4 w-4" weight="bold" />
                </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" side="right" className="w-36">
                <DropdownMenuItem onClick={onRename}>
                    <PencilSimple className="h-3.5 w-3.5 mr-2" />
                    Rename
                </DropdownMenuItem>
                {showPin && onTogglePin && (
                    <DropdownMenuItem onClick={onTogglePin}>
                        <PushPin className="h-3.5 w-3.5 mr-2" weight={isPinned ? 'fill' : 'regular'} />
                        {isPinned ? 'Unpin' : 'Pin'}
                    </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                    onClick={onDelete}
                    className="text-destructive focus:text-destructive"
                >
                    <Trash className="h-3.5 w-3.5 mr-2" />
                    Delete
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
});

// Backend session item with options menu (supports pin)
const SessionItem = memo(function SessionItem({
    session,
    isActive,
    onSelect,
}: {
    session: BackendChatSession;
    isActive: boolean;
    onSelect: (id: string) => void;
}) {
    const [mode, setMode] = useState<'view' | 'rename' | 'delete'>('view');
    const updateSession = useUpdateChatSession();
    const deleteSession = useDeleteChatSession();

    const handleRename = useCallback((newTitle: string) => {
        updateSession.mutate({ id: session.id, data: { title: newTitle } });
        setMode('view');
    }, [session.id, updateSession]);

    const handleTogglePin = useCallback(() => {
        updateSession.mutate({ id: session.id, data: { is_pinned: !session.is_pinned } });
    }, [session.id, session.is_pinned, updateSession]);

    const handleDelete = useCallback(() => {
        deleteSession.mutate(session.id);
        setMode('view');
    }, [session.id, deleteSession]);

    if (mode === 'rename') {
        return (
            <RenameInput
                initialValue={session.title || 'New Chat'}
                onSave={handleRename}
                onCancel={() => setMode('view')}
            />
        );
    }

    if (mode === 'delete') {
        return (
            <DeleteConfirmation
                onConfirm={handleDelete}
                onCancel={() => setMode('view')}
            />
        );
    }

    return (
        <div
            className={cn(
                'w-full flex items-center gap-1 px-2 py-1.5 rounded-md transition-colors text-sm text-left group cursor-pointer',
                isActive ? 'bg-muted' : 'hover:bg-muted'
            )}
        >
            <button
                onClick={() => onSelect(session.id)}
                className="flex-1 truncate text-left min-w-0"
            >
                {session.title || 'New Chat'}
            </button>
            <SessionOptionsMenu
                onRename={() => setMode('rename')}
                onDelete={() => setMode('delete')}
                onTogglePin={handleTogglePin}
                isPinned={session.is_pinned}
            />
        </div>
    );
});

// Engine conversation item (open only; navigates with ?c=)
const EngineSessionItem = memo(function EngineSessionItem({
    item,
    isActive,
    onSelect,
}: {
    item: ConversationListItem;
    isActive: boolean;
    onSelect: (item: ConversationListItem) => void;
}) {
    return (
        <div
            className={cn(
                'w-full flex items-center gap-1 px-2 py-1.5 rounded-md transition-colors text-sm text-left group cursor-pointer',
                isActive ? 'bg-muted' : 'hover:bg-muted'
            )}
        >
            <button onClick={() => onSelect(item)} className="flex-1 truncate text-left min-w-0">
                {item.title || 'New Chat'}
            </button>
        </div>
    );
});

// Local (localStorage) session item with options menu (rename + delete only)
const LocalSessionItem = memo(function LocalSessionItem({
    session,
    isActive,
    onSelect,
}: {
    session: { id: string; title: string };
    isActive: boolean;
    onSelect: (id: string) => void;
}) {
    const [mode, setMode] = useState<'view' | 'rename' | 'delete'>('view');
    const { updateSessionTitle, deleteSession } = useChatSessionsStore();

    const handleRename = useCallback((newTitle: string) => {
        updateSessionTitle(session.id, newTitle);
        setMode('view');
    }, [session.id, updateSessionTitle]);

    const handleDelete = useCallback(() => {
        deleteSession(session.id);
        setMode('view');
    }, [session.id, deleteSession]);

    if (mode === 'rename') {
        return (
            <RenameInput
                initialValue={session.title || 'New Chat'}
                onSave={handleRename}
                onCancel={() => setMode('view')}
            />
        );
    }

    if (mode === 'delete') {
        return (
            <DeleteConfirmation
                onConfirm={handleDelete}
                onCancel={() => setMode('view')}
            />
        );
    }

    return (
        <div
            className={cn(
                'w-full flex items-center gap-1 px-2 py-1.5 rounded-md transition-colors text-sm text-left group cursor-pointer',
                isActive ? 'bg-muted' : 'hover:bg-muted'
            )}
        >
            <button
                onClick={() => onSelect(session.id)}
                className="flex-1 truncate text-left min-w-0"
            >
                {session.title || 'New Chat'}
            </button>
            <SessionOptionsMenu
                onRename={() => setMode('rename')}
                onDelete={() => setMode('delete')}
                showPin={false}
            />
        </div>
    );
});

export const ChatSidebar = memo(function ChatSidebar({ className }: ChatSidebarProps) {
    const router = useRouter();
    const {
        currentSessionId,
        createSession,
        setCurrentSession,
    } = useChatSessionsStore();

    const {
        projects,
        currentProjectId,
        setCurrentProject,
        createProject,
        loadProjects,
    } = useProjectsStore();

    // Subscribe reactively to sessions array so sidebar re-renders on new sessions
    const storeSessions = useChatSessionsStore((s) => s.sessions);

    const { data: agents } = useAgents();
    const { data: backendSessions, isLoading: sessionsLoading } = useChatSessionsList();

    // Track mounted state to avoid hydration mismatch
    const [hasMounted, setHasMounted] = useState(false);
    useEffect(() => {
        setHasMounted(true);
        loadProjects();
    }, [loadProjects]);

    // Default agents if API fails
    const defaultAgents = [
        { id: BUILTIN_AGENT_UUIDS['data-retrieval'], name: 'Data Retrieval', description: 'Fetch financial data' },
        { id: BUILTIN_AGENT_UUIDS['analysis'], name: 'Analysis', description: 'Analyze metrics' },
        { id: BUILTIN_AGENT_UUIDS['compliance'], name: 'Compliance', description: 'Check regulations' },
        { id: BUILTIN_AGENT_UUIDS['research'], name: 'Research', description: 'Research documents' },
    ];

    // Filter out General Assistant from specialized list (it has its own section)
    const displayAgents = agents && agents.length > 0
        ? agents.filter(a => a.id !== BUILTIN_AGENT_UUIDS['general'])
        : defaultAgents;

    // Use backend sessions when available, fall back to localStorage
    const localSessions = useMemo(() => {
        if (!hasMounted) return [];
        return storeSessions
            .filter((s) => !s.projectId)
            .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
            .slice(0, 10);
    }, [hasMounted, storeSessions]);
    const sessions = backendSessions ?? [];

    const engineMode = useEngineChatMode() === 'engine';
    const { data: unionItems } = useConversationUnion(engineMode);
    const engineNav = useEngineNav();
    const engineItems = useMemo(
        () => (engineMode ? (unionItems ?? []).filter((i) => i.source === 'engine') : []),
        [engineMode, unionItems]
    );

    // Split into pinned and recent
    const pinnedSessions = sessions.filter((s) => s.is_pinned);
    const recentSessions = sessions.filter((s) => !s.is_pinned);
    // Recents: legacy sessions (unchanged behavior) with engine conversations merged in by recency.
    const recentRows = useMemo(() => {
        type Row = { kind: 'legacy'; at: number; session: BackendChatSession } | { kind: 'engine'; at: number; item: ConversationListItem };
        const rows: Row[] = [
            ...recentSessions.map((session): Row => ({ kind: 'legacy', at: new Date(session.updated_at).getTime(), session })),
            ...engineItems.map((item): Row => ({ kind: 'engine', at: new Date(item.updated_at).getTime(), item })),
        ];
        return engineItems.length ? rows.sort((a, b) => b.at - a.at) : rows;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [backendSessions, engineItems]);

    // Show backend sessions if available, otherwise localStorage fallback
    const hasBackendSessions = (backendSessions !== undefined && backendSessions.length > 0) || engineItems.length > 0;

    const handleSelectEngine = (item: ConversationListItem) => {
        setCurrentSession(null);
        engineNav.open(item.id);
        if (item.project_id) {
            setCurrentProject(item.project_id);
            router.push(`/chat/projects/${item.project_id}?c=${encodeURIComponent(item.id)}`);
        } else {
            if (currentProjectId) setCurrentProject(null);
            router.push(`/chat?c=${encodeURIComponent(item.id)}`);
        }
    };

    const handleNewSession = () => {
        if (engineMode) engineNav.open(null);
        const id = createSession();
        setCurrentSession(id);
        if (currentProjectId) {
            setCurrentProject(null);
            router.push('/chat');
        }
    };

    const handleSelectAgent = (agentId: string) => {
        const sessionId = createSession(agentId);
        setCurrentSession(sessionId);
    };

    const [agentBrowserOpen, setAgentBrowserOpen] = useState(false);

    const handleMoreAgents = () => {
        setAgentBrowserOpen(true);
    };

    const handleNewProject = async () => {
        const name = `Project ${projects.length + 1}`;
        const project = await createProject(name);
        router.push(`/chat/projects/${project.id}`);
    };

    const handleSelectProject = (projectId: string) => {
        setCurrentProject(projectId);
        setCurrentSession(null);
        router.push(`/chat/projects/${projectId}`);
    };

    const handleSelectSession = (sessionId: string) => {
        setCurrentSession(sessionId);
        if (currentProjectId) {
            setCurrentProject(null);
            router.push('/chat');
        }
    };

    return (
        <TooltipProvider delayDuration={300}>
            <aside className={cn(
                'w-[220px] border-r border-border bg-background/50 h-full flex flex-col shrink-0',
                className
            )}>
                {/* New Session Button */}
                <div className="p-3">
                    <button
                        onClick={handleNewSession}
                        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg border border-border hover:bg-muted transition-colors text-sm font-medium"
                    >
                        <PencilSimpleLine className="h-4 w-4" />
                        New Session
                    </button>
                </div>

                {/* Scrollable Content */}
                <div className="flex-1 overflow-y-auto px-3 pb-3">
                    {/* Agents Section */}
                    <div className="mb-4">
                        <div className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide mb-2 px-1">
                            Agents
                        </div>
                        <div className="space-y-0.5">
                            {displayAgents.slice(0, 3).map((agent) => (
                                <button
                                    key={agent.id}
                                    onClick={() => handleSelectAgent(agent.id)}
                                    className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors text-sm text-left"
                                >
                                    <Robot className="h-4 w-4 text-muted-foreground" />
                                    <span className="truncate">{agent.name}</span>
                                </button>
                            ))}
                            <button
                                onClick={handleMoreAgents}
                                className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors text-sm text-muted-foreground"
                            >
                                <DotsThree className="h-4 w-4" weight="bold" />
                                <span>More Agents</span>
                            </button>
                        </div>
                    </div>

                    {/* Projects Section */}
                    <div className="mb-4">
                        <div className="flex items-center justify-between mb-2 px-1">
                            <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
                                Projects
                            </span>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <button
                                        onClick={handleNewProject}
                                        className="h-5 w-5 flex items-center justify-center rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                                    >
                                        <Plus className="h-3.5 w-3.5" />
                                    </button>
                                </TooltipTrigger>
                                <TooltipContent side="right">
                                    <p>New Project</p>
                                </TooltipContent>
                            </Tooltip>
                        </div>
                        <div className="space-y-0.5">
                            {projects.length === 0 ? (
                                <p className="text-xs text-muted-foreground px-2 py-1">No projects yet</p>
                            ) : (
                                projects.map((project) => (
                                    <button
                                        key={project.id}
                                        onClick={() => handleSelectProject(project.id)}
                                        className={cn(
                                            'w-full flex items-center gap-2 px-2 py-1.5 rounded-md transition-colors text-sm text-left',
                                            currentProjectId === project.id
                                                ? 'bg-muted'
                                                : 'hover:bg-muted'
                                        )}
                                    >
                                        <FolderSimple className="h-4 w-4 text-muted-foreground" />
                                        <span className="truncate">{project.name}</span>
                                    </button>
                                ))
                            )}
                        </div>
                    </div>

                    {/* Pinned Sessions */}
                    {hasBackendSessions && pinnedSessions.length > 0 && (
                        <div className="mb-4">
                            <div className="flex items-center gap-1 mb-2 px-1">
                                <PushPin className="h-3 w-3 text-muted-foreground" weight="fill" />
                                <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
                                    Pinned
                                </span>
                            </div>
                            <div className="space-y-0.5">
                                {pinnedSessions.map((session) => (
                                    <SessionItem
                                        key={session.id}
                                        session={session}
                                        isActive={currentSessionId === session.id && !currentProjectId}
                                        onSelect={handleSelectSession}
                                    />
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Recents Section */}
                    <div>
                        <div className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide mb-2 px-1">
                            Recents
                        </div>
                        <div className="space-y-0.5">
                            {sessionsLoading && !hasMounted ? (
                                // Skeleton loading state
                                Array.from({ length: 3 }).map((_, i) => (
                                    <div key={i} className="h-8 rounded-md bg-muted/50 animate-pulse mx-2" />
                                ))
                            ) : hasBackendSessions ? (
                                recentRows.length === 0 ? (
                                    <p className="text-xs text-muted-foreground px-2 py-1">No recent chats</p>
                                ) : (
                                    recentRows.map((row) =>
                                        row.kind === 'engine' ? (
                                            <EngineSessionItem
                                                key={`e-${row.item.id}`}
                                                item={row.item}
                                                isActive={engineNav.conversationId === row.item.id}
                                                onSelect={handleSelectEngine}
                                            />
                                        ) : (
                                            <SessionItem
                                                key={row.session.id}
                                                session={row.session}
                                                isActive={currentSessionId === row.session.id && !currentProjectId}
                                                onSelect={handleSelectSession}
                                            />
                                        )
                                    )
                                )
                            ) : (
                                // Fallback to localStorage sessions
                                localSessions.length === 0 ? (
                                    <p className="text-xs text-muted-foreground px-2 py-1">No recent chats</p>
                                ) : (
                                    localSessions.map((session) => (
                                        <LocalSessionItem
                                            key={session.id}
                                            session={session}
                                            isActive={currentSessionId === session.id && !currentProjectId}
                                            onSelect={handleSelectSession}
                                        />
                                    ))
                                )
                            )}
                        </div>
                    </div>
                </div>

                <AgentBrowserModal
                    open={agentBrowserOpen}
                    onOpenChange={setAgentBrowserOpen}
                    onSelectAgent={handleSelectAgent}
                />
            </aside>
        </TooltipProvider>
    );
});
