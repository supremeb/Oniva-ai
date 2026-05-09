import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { SUPPORTED_MODELS } from '@/lib/models';
import type { MessagePart, SourceItem } from '@/lib/models';

// ---------------------------------------------------------------------------
// localStorage persistence helpers: debounced writes + LRU eviction
// ---------------------------------------------------------------------------

const MAX_SESSIONS = 50;
const MAX_STORAGE_BYTES = 2 * 1024 * 1024; // 2 MB
const DEBOUNCE_MS = 1_000;

/** Simple trailing-edge debounce. Returns a debounced wrapper. */
function createDebouncedSave(fn: (name: string, value: unknown) => void) {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let latestArgs: [string, unknown] | null = null;

    return (name: string, value: unknown) => {
        latestArgs = [name, value];
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
            if (latestArgs) fn(...latestArgs);
            timer = null;
            latestArgs = null;
        }, DEBOUNCE_MS);
    };
}

/**
 * Evict oldest sessions so that:
 *  1. At most MAX_SESSIONS remain (LRU by updatedAt)
 *  2. Serialised size stays under MAX_STORAGE_BYTES
 *
 * Mutates `state.sessions` in-place on the shallow clone passed in and returns
 * the (potentially trimmed) value object ready for JSON.stringify.
 */
function evictSessions(value: Record<string, unknown>): Record<string, unknown> {
    const state = value.state as Record<string, unknown> | undefined;
    if (!state?.sessions || !Array.isArray(state.sessions)) return value;

    // Sort descending by updatedAt (most recent first)
    let sessions: ChatSession[] = [...(state.sessions as ChatSession[])].sort(
        (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
    );

    // 1. Cap at MAX_SESSIONS
    if (sessions.length > MAX_SESSIONS) {
        sessions = sessions.slice(0, MAX_SESSIONS);
    }

    // 2. Trim until serialised size is within budget
    let candidate = { ...value, state: { ...state, sessions } };
    let serialised = JSON.stringify(candidate);

    while (serialised.length > MAX_STORAGE_BYTES && sessions.length > 1) {
        sessions = sessions.slice(0, -1); // drop the oldest
        candidate = { ...value, state: { ...state, sessions } };
        serialised = JSON.stringify(candidate);
    }

    return candidate;
}

// Types
export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    reasoning?: string;
    /** Rich structured parts for MessageRenderer */
    richParts?: MessagePart[];
    /** Accumulated sources for the message */
    sources?: SourceItem[];
    timestamp: Date;
}

export interface ChatSession {
    id: string;
    title: string;
    agentId: string | null;
    projectId: string | null;
    messages: ChatMessage[];
    activeConnectorIds: string[];
    createdAt: Date;
    updatedAt: Date;
}

export interface ChatProject {
    id: string;
    name: string;
    instructions: string;
    files: ProjectFile[];
    createdAt: Date;
    updatedAt: Date;
}

export interface ProjectFile {
    id: string;
    name: string;
    type: string;
    size: number;
    uploadedAt: Date;
}

export interface ActiveSources {
    webSearchEnabled: boolean;
    specificSites: string[];
    activeKnowledgeBaseIds: string[];
}

const DEFAULT_ACTIVE_SOURCES: ActiveSources = {
    webSearchEnabled: false,
    specificSites: [],
    activeKnowledgeBaseIds: [],
};

interface ChatSessionsStore {
    // State
    sessions: ChatSession[];
    projects: ChatProject[];
    currentSessionId: string | null;
    currentProjectId: string | null;
    selectedModel: string | null;
    thinkingEnabled: boolean;

    // Pending input from project view → chat interface
    pendingInput: string | null;

    // Ephemeral source selection state (not persisted)
    activeSources: Record<string, ActiveSources>;
    activeSkillIds: Record<string, string[]>;

    // Session actions
    createSession: (agentId?: string | null, projectId?: string | null) => string;
    deleteSession: (sessionId: string) => void;
    setCurrentSession: (sessionId: string | null) => void;
    updateSessionTitle: (sessionId: string, title: string) => void;
    addMessageToSession: (sessionId: string, message: Omit<ChatMessage, 'id' | 'timestamp'>) => void;
    updateLastMessageInSession: (sessionId: string, content: string) => void;
    updateLastAssistantInSession: (sessionId: string, updates: Partial<ChatMessage>) => void;
    getSession: (sessionId: string) => ChatSession | undefined;
    migrateSession: (fromId: string, toId: string) => void;
    getRecentSessions: (limit?: number) => ChatSession[];
    toggleConnector: (sessionId: string, connectorId: string) => void;

    // Source actions (ephemeral)
    setActiveSources: (sessionId: string, sources: Partial<ActiveSources>) => void;
    toggleSource: (sessionId: string, type: 'web' | 'kb' | 'site', id?: string) => void;
    resetSources: (sessionId: string) => void;
    getActiveSources: (sessionId: string) => ActiveSources;

    // Skill actions (ephemeral — persists per session, resets on new session)
    setActiveSkillIds: (sessionId: string, skillIds: string[]) => void;
    addActiveSkillId: (sessionId: string, skillId: string) => void;
    removeActiveSkillId: (sessionId: string, skillId: string) => void;
    getActiveSkillIds: (sessionId: string) => string[];

    // Project actions
    createProject: (name: string) => string;
    deleteProject: (projectId: string) => void;
    setCurrentProject: (projectId: string | null) => void;
    updateProjectInstructions: (projectId: string, instructions: string) => void;
    addFileToProject: (projectId: string, file: Omit<ProjectFile, 'id' | 'uploadedAt'>) => void;
    removeFileFromProject: (projectId: string, fileId: string) => void;
    getProject: (projectId: string) => ChatProject | undefined;
    getProjectSessions: (projectId: string) => ChatSession[];

    // Model selection
    setSelectedModel: (model: string | null) => void;

    // Thinking mode
    setThinkingEnabled: (enabled: boolean) => void;

    // Pending input
    setPendingInput: (input: string | null) => void;
    consumePendingInput: () => string | null;
}

export const useChatSessionsStore = create<ChatSessionsStore>()(
    persist(
        (set, get) => ({
            // Initial state
            sessions: [],
            projects: [],
            currentSessionId: null,
            currentProjectId: null,
            selectedModel: null,
            thinkingEnabled: false,
            pendingInput: null,
            activeSources: {},
            activeSkillIds: {},

            // Session actions
            createSession: (agentId = null, projectId = null) => {
                const id = crypto.randomUUID();
                const now = new Date();
                const newSession: ChatSession = {
                    id,
                    title: 'New Chat',
                    agentId,
                    projectId,
                    messages: [],
                    activeConnectorIds: [],
                    createdAt: now,
                    updatedAt: now,
                };
                set((state) => ({
                    sessions: [newSession, ...state.sessions],
                    currentSessionId: id,
                }));
                return id;
            },

            deleteSession: (sessionId) => {
                set((state) => ({
                    sessions: state.sessions.filter((s) => s.id !== sessionId),
                    currentSessionId: state.currentSessionId === sessionId ? null : state.currentSessionId,
                }));
            },

            setCurrentSession: (sessionId) => {
                set({ currentSessionId: sessionId });
            },

            updateSessionTitle: (sessionId, title) => {
                set((state) => ({
                    sessions: state.sessions.map((s) =>
                        s.id === sessionId ? { ...s, title, updatedAt: new Date() } : s
                    ),
                }));
            },

            addMessageToSession: (sessionId, message) => {
                const newMessage: ChatMessage = {
                    ...message,
                    id: crypto.randomUUID(),
                    timestamp: new Date(),
                };
                set((state) => ({
                    sessions: state.sessions.map((s) => {
                        if (s.id !== sessionId) return s;
                        const updatedSession = {
                            ...s,
                            messages: [...s.messages, newMessage],
                            updatedAt: new Date(),
                        };
                        // Auto-update title from first user message
                        if (s.title === 'New Chat' && message.role === 'user') {
                            updatedSession.title = message.content.slice(0, 50) + (message.content.length > 50 ? '...' : '');
                        }
                        return updatedSession;
                    }),
                }));
            },

            updateLastMessageInSession: (sessionId, content) => {
                set((state) => ({
                    sessions: state.sessions.map((s) => {
                        if (s.id !== sessionId) return s;
                        const messages = [...s.messages];
                        if (messages.length > 0) {
                            messages[messages.length - 1] = {
                                ...messages[messages.length - 1],
                                content,
                            };
                        }
                        return { ...s, messages, updatedAt: new Date() };
                    }),
                }));
            },

            updateLastAssistantInSession: (sessionId, updates) => {
                set((state) => ({
                    sessions: state.sessions.map((s) => {
                        if (s.id !== sessionId) return s;
                        const messages = [...s.messages];
                        // Find the last assistant message
                        for (let i = messages.length - 1; i >= 0; i--) {
                            if (messages[i].role === 'assistant') {
                                messages[i] = { ...messages[i], ...updates };
                                break;
                            }
                        }
                        return { ...s, messages, updatedAt: new Date() };
                    }),
                }));
            },

            getSession: (sessionId) => {
                return get().sessions.find((s) => s.id === sessionId);
            },

            migrateSession: (fromId, toId) => {
                set((state) => {
                    const session = state.sessions.find((s) => s.id === fromId);
                    if (!session) return state;
                    const updatedSources = { ...state.activeSources };
                    if (updatedSources[fromId]) {
                        updatedSources[toId] = updatedSources[fromId];
                        delete updatedSources[fromId];
                    }
                    const updatedSkills = { ...state.activeSkillIds };
                    if (updatedSkills[fromId]) {
                        updatedSkills[toId] = updatedSkills[fromId];
                        delete updatedSkills[fromId];
                    }
                    return {
                        sessions: state.sessions.map((s) =>
                            s.id === fromId ? { ...s, id: toId } : s
                        ),
                        currentSessionId: state.currentSessionId === fromId ? toId : state.currentSessionId,
                        activeSources: updatedSources,
                        activeSkillIds: updatedSkills,
                    };
                });
            },

            getRecentSessions: (limit = 10) => {
                return get()
                    .sessions
                    .filter((s) => !s.projectId) // Exclude project sessions from recents
                    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
                    .slice(0, limit);
            },

            toggleConnector: (sessionId, connectorId) => {
                set((state) => ({
                    sessions: state.sessions.map((s) => {
                        if (s.id !== sessionId) return s;
                        const ids = s.activeConnectorIds ?? [];
                        const next = ids.includes(connectorId)
                            ? ids.filter((id) => id !== connectorId)
                            : [...ids, connectorId];
                        return { ...s, activeConnectorIds: next, updatedAt: new Date() };
                    }),
                }));
            },

            // Source actions (ephemeral — not persisted)
            setActiveSources: (sessionId, sources) => {
                set((state) => ({
                    activeSources: {
                        ...state.activeSources,
                        [sessionId]: {
                            ...(state.activeSources[sessionId] ?? DEFAULT_ACTIVE_SOURCES),
                            ...sources,
                        },
                    },
                }));
            },

            toggleSource: (sessionId, type, id) => {
                set((state) => {
                    const current = state.activeSources[sessionId] ?? DEFAULT_ACTIVE_SOURCES;
                    let updated: ActiveSources;

                    switch (type) {
                        case 'web':
                            updated = { ...current, webSearchEnabled: !current.webSearchEnabled };
                            break;
                        case 'kb':
                            if (!id) return state;
                            updated = {
                                ...current,
                                activeKnowledgeBaseIds: current.activeKnowledgeBaseIds.includes(id)
                                    ? current.activeKnowledgeBaseIds.filter((kbId) => kbId !== id)
                                    : [...current.activeKnowledgeBaseIds, id],
                            };
                            break;
                        case 'site':
                            if (!id) return state;
                            updated = {
                                ...current,
                                specificSites: current.specificSites.includes(id)
                                    ? current.specificSites.filter((s) => s !== id)
                                    : current.specificSites.length < 10
                                        ? [...current.specificSites, id]
                                        : current.specificSites,
                            };
                            break;
                    }

                    return {
                        activeSources: {
                            ...state.activeSources,
                            [sessionId]: updated,
                        },
                    };
                });
            },

            resetSources: (sessionId) => {
                set((state) => ({
                    activeSources: {
                        ...state.activeSources,
                        [sessionId]: { ...DEFAULT_ACTIVE_SOURCES },
                    },
                }));
            },

            getActiveSources: (sessionId) => {
                return get().activeSources[sessionId] ?? DEFAULT_ACTIVE_SOURCES;
            },

            // Skill actions (ephemeral)
            setActiveSkillIds: (sessionId, skillIds) => {
                set((state) => ({
                    activeSkillIds: { ...state.activeSkillIds, [sessionId]: skillIds },
                }));
            },

            addActiveSkillId: (sessionId, skillId) => {
                set((state) => {
                    const current = state.activeSkillIds[sessionId] ?? [];
                    if (current.includes(skillId)) return state;
                    return {
                        activeSkillIds: { ...state.activeSkillIds, [sessionId]: [...current, skillId] },
                    };
                });
            },

            removeActiveSkillId: (sessionId, skillId) => {
                set((state) => {
                    const current = state.activeSkillIds[sessionId] ?? [];
                    return {
                        activeSkillIds: { ...state.activeSkillIds, [sessionId]: current.filter((id) => id !== skillId) },
                    };
                });
            },

            getActiveSkillIds: (sessionId) => {
                return get().activeSkillIds[sessionId] ?? [];
            },

            // Project actions
            createProject: (name) => {
                const id = crypto.randomUUID();
                const now = new Date();
                const newProject: ChatProject = {
                    id,
                    name,
                    instructions: '',
                    files: [],
                    createdAt: now,
                    updatedAt: now,
                };
                set((state) => ({
                    projects: [newProject, ...state.projects],
                    currentProjectId: id,
                }));
                return id;
            },

            deleteProject: (projectId) => {
                set((state) => ({
                    projects: state.projects.filter((p) => p.id !== projectId),
                    sessions: state.sessions.filter((s) => s.projectId !== projectId),
                    currentProjectId: state.currentProjectId === projectId ? null : state.currentProjectId,
                }));
            },

            setCurrentProject: (projectId) => {
                set({ currentProjectId: projectId, currentSessionId: null });
            },

            updateProjectInstructions: (projectId, instructions) => {
                set((state) => ({
                    projects: state.projects.map((p) =>
                        p.id === projectId ? { ...p, instructions, updatedAt: new Date() } : p
                    ),
                }));
            },

            addFileToProject: (projectId, file) => {
                const newFile: ProjectFile = {
                    ...file,
                    id: crypto.randomUUID(),
                    uploadedAt: new Date(),
                };
                set((state) => ({
                    projects: state.projects.map((p) =>
                        p.id === projectId
                            ? { ...p, files: [...p.files, newFile], updatedAt: new Date() }
                            : p
                    ),
                }));
            },

            removeFileFromProject: (projectId, fileId) => {
                set((state) => ({
                    projects: state.projects.map((p) =>
                        p.id === projectId
                            ? { ...p, files: p.files.filter((f) => f.id !== fileId), updatedAt: new Date() }
                            : p
                    ),
                }));
            },

            getProject: (projectId) => {
                return get().projects.find((p) => p.id === projectId);
            },

            getProjectSessions: (projectId) => {
                return get()
                    .sessions
                    .filter((s) => s.projectId === projectId)
                    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
            },

            // Model selection
            setSelectedModel: (model) => {
                set({ selectedModel: model });
            },

            // Thinking mode
            setThinkingEnabled: (enabled) => {
                set({ thinkingEnabled: enabled });
            },

            setPendingInput: (input) => {
                set({ pendingInput: input });
            },

            consumePendingInput: () => {
                const { pendingInput } = get();
                if (pendingInput) {
                    set({ pendingInput: null });
                }
                return pendingInput;
            },
        }),
        {
            name: 'oniva-chat-sessions',
            version: 1,
            partialize: (state) => ({
                sessions: state.sessions,
                projects: state.projects,
                currentSessionId: state.currentSessionId,
                currentProjectId: state.currentProjectId,
                selectedModel: state.selectedModel,
                thinkingEnabled: state.thinkingEnabled,
                // activeSources intentionally excluded — ephemeral per page load
            }),
            // Quota-safe localStorage with debounced writes + LRU eviction
            storage: {
                getItem: (name) => {
                    const raw = localStorage.getItem(name);
                    return raw ? JSON.parse(raw) : null;
                },
                setItem: (() => {
                    /** Performs the actual write with eviction + quota fallback. */
                    function commitToLocalStorage(name: string, value: unknown) {
                        // Run LRU eviction before serialising
                        const evicted = evictSessions(value as Record<string, unknown>);
                        try {
                            localStorage.setItem(name, JSON.stringify(evicted));
                        } catch (e) {
                            if (e instanceof DOMException && e.name === 'QuotaExceededError') {
                                // Strip richParts from all messages and retry
                                const trimmed = { ...(evicted as Record<string, unknown>) };
                                const tState = trimmed.state as Record<string, unknown> | undefined;
                                if (tState?.sessions) {
                                    trimmed.state = {
                                        ...tState,
                                        sessions: (tState.sessions as ChatSession[]).map((s: ChatSession) => ({
                                            ...s,
                                            messages: s.messages.map((m: ChatMessage) => {
                                                const { richParts, sources, ...rest } = m;
                                                return rest;
                                            }),
                                        })),
                                    };
                                }
                                try {
                                    localStorage.setItem(name, JSON.stringify(trimmed));
                                } catch {
                                    // Still failing — give up silently
                                    console.warn('[chat-sessions] localStorage quota exceeded, unable to persist');
                                }
                            }
                        }
                    }

                    // Return a 1-second debounced version
                    return createDebouncedSave(commitToLocalStorage);
                })(),
                removeItem: (name) => localStorage.removeItem(name),
            },
            migrate: (persisted, version) => {
                const state = persisted as Record<string, unknown>;
                if (version === 0) {
                    // v0→v1: Clear deprecated models not in SUPPORTED_MODELS
                    const validIds = new Set(SUPPORTED_MODELS.map((m) => m.id));
                    const savedModel = state.selectedModel as string | null;
                    if (savedModel && !validIds.has(savedModel)) {
                        state.selectedModel = null;
                    }
                }
                return state as ChatSessionsStore;
            },
        }
    )
);
