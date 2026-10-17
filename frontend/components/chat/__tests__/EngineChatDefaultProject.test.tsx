/**
 * U1 (KTD1): a first message on `/chat` with no project in the URL resolves the most
 * recently updated project the user owns, or creates a private "Personal" project when
 * the user owns none. Shared projects are never auto-picked (AE1).
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TooltipProvider } from '@/components/ui/tooltip';

const mockFetchProjects = jest.fn();
const mockCreateProject = jest.fn();
jest.mock('@/lib/api/projects', () => ({
    fetchProjects: (...a: unknown[]) => mockFetchProjects(...a),
    createProject: (...a: unknown[]) => mockCreateProject(...a),
    fetchProject: jest.fn(),
    updateProject: jest.fn(),
    archiveProject: jest.fn(),
}));

const mockClarifyEngine = jest.fn();
const mockPlanEngine = jest.fn();
jest.mock('@/lib/engine/deepWorkApi', () => ({
    clarifyEngine: (...a: unknown[]) => mockClarifyEngine(...a),
    planEngine: (...a: unknown[]) => mockPlanEngine(...a),
    startEngineRun: jest.fn(async () => ({ session_id: 'dw', conversation_id: 'conv', status: 'started' })),
    getEngineFullState: jest.fn(async () => null),
}));

jest.mock('react-markdown', () => ({ __esModule: true, default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div> }));
jest.mock('remark-gfm', () => ({ __esModule: true, default: () => {} }));
jest.mock('@/components/ui/GenerativeO', () => ({ GenerativeO: () => null, GenerativeOText: () => null }));
jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@/components/chat/ChatSelections', () => ({
    buildSelectionPopovers: () => ({ webSearchPopover: null, knowledgePopover: null }),
}));
jest.mock('@/components/chat/EngineSkillConnectorPickers', () => ({ EngineSkillConnectorPickers: () => null }));
jest.mock('@/lib/api', () => {
    class APIError extends Error {
        constructor(message: string, public status?: number, public data?: unknown) {
            super(message);
        }
    }
    return { API_BASE_URL: 'http://api.test', APIError, apiFetch: jest.fn(), attemptTokenRefresh: jest.fn(async () => true) };
});

import { EngineChat } from '../EngineChat';
import { useAuthStore } from '@/lib/stores/auth';
import { useProjectsStore } from '@/lib/stores/projects';
import { useEngineChatPrefs } from '@/lib/stores/engine-chat-prefs';
import { useDeepWorkStore } from '@/lib/stores/deep-work';
import { useEngineSessionStore } from '@/components/deep-work/engineSession';
import type { EngineConversationClient } from '@/lib/engine';
import type { Project, ProjectListResponse } from '@/lib/api/projects';

const makeProject = (over: Partial<Project>): Project => ({
    id: 'p',
    tenant_id: 't',
    owner_id: 'u',
    name: 'P',
    description: null,
    instructions: null,
    default_model: null,
    knowledge_base_id: null,
    settings: {},
    is_archived: false,
    row_version: 1,
    member_count: 1,
    file_count: 0,
    session_count: 0,
    user_role: 'owner',
    created_at: '2026-01-01T00:00:00.000Z',
    updated_at: '2026-01-01T00:00:00.000Z',
    ...over,
});
const list = (projects: Project[]): ProjectListResponse => ({ projects, total: projects.length, page: 1, page_size: 100 });

function fakeClient() {
    return {
        create: jest.fn(async (projectId: string) => ({ conversationId: 'conv-new', projectId })),
        watch: jest.fn(() => new Promise(() => {})),
        submit: jest.fn(async () => ({})),
        abort: jest.fn(async () => ({})),
        updateSelections: jest.fn(async () => ({})),
    };
}

const box = () => screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const submit = async () => {
    await act(async () => {
        fireEvent.keyDown(box(), { key: 'Enter' });
    });
};
const mount = (client: ReturnType<typeof fakeClient>) =>
    render(
        <TooltipProvider>
            <EngineChat client={client as unknown as EngineConversationClient} />
        </TooltipProvider>
    );

beforeAll(() => {
    window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});

beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    useEngineChatPrefs.setState({ drafts: {} });
    useProjectsStore.setState({ projects: [], isLoading: false, error: null, currentProjectId: null });
    useAuthStore.setState({ user: { features: {} } } as never);
    useDeepWorkStore.getState().reset();
    useEngineSessionStore.setState({ session: null, terminal: null });
    window.history.replaceState(null, '', '/chat');
});

describe('default project for a first message', () => {
    it('no projects: creates "Personal", then starts the conversation, and shows no error', async () => {
        mockFetchProjects.mockResolvedValue(list([]));
        mockCreateProject.mockResolvedValue(makeProject({ id: 'personal', name: 'Personal' }));
        const client = fakeClient();
        mount(client);

        type('hello');
        await submit();

        await waitFor(() => expect(client.create).toHaveBeenCalledWith('personal', expect.any(Object)));
        expect(mockCreateProject).toHaveBeenCalledWith(expect.objectContaining({ name: 'Personal' }));
        await waitFor(() => expect(client.submit).toHaveBeenCalledTimes(1));
        expect(screen.queryByText(/pick or create a project/i)).toBeNull();
    });

    it('two owned projects: uses the most recently updated one', async () => {
        mockFetchProjects.mockResolvedValue(
            list([
                makeProject({ id: 'old', updated_at: '2026-01-01T00:00:00.000Z' }),
                makeProject({ id: 'new', updated_at: '2026-06-01T00:00:00.000Z' }),
            ])
        );
        const client = fakeClient();
        mount(client);

        type('hello');
        await submit();

        await waitFor(() => expect(client.create).toHaveBeenCalledWith('new', expect.any(Object)));
        expect(mockCreateProject).not.toHaveBeenCalled();
    });

    it('project creation fails: inline message, draft kept, no conversation started', async () => {
        mockFetchProjects.mockResolvedValue(list([]));
        mockCreateProject.mockRejectedValue(new Error('forbidden'));
        const client = fakeClient();
        mount(client);

        type('keep me');
        await submit();

        expect(await screen.findByText(/pick or create a project/i)).toBeTruthy();
        expect(client.create).not.toHaveBeenCalled();
        expect(box().value).toBe('keep me');
    });

    it('never picks a newer shared project over an owned one', async () => {
        mockFetchProjects.mockResolvedValue(
            list([
                makeProject({ id: 'mine', user_role: 'owner', updated_at: '2026-01-01T00:00:00.000Z' }),
                makeProject({ id: 'shared', user_role: 'editor', updated_at: '2026-09-01T00:00:00.000Z' }),
            ])
        );
        const client = fakeClient();
        mount(client);

        type('hello');
        await submit();

        await waitFor(() => expect(client.create).toHaveBeenCalledWith('mine', expect.any(Object)));
        expect(client.create).not.toHaveBeenCalledWith('shared', expect.anything());
    });

    it('owns none (only shared projects): creates "Personal" instead of picking one', async () => {
        mockFetchProjects.mockResolvedValue(list([makeProject({ id: 'shared', user_role: 'viewer' })]));
        mockCreateProject.mockResolvedValue(makeProject({ id: 'personal', name: 'Personal' }));
        const client = fakeClient();
        mount(client);

        type('hello');
        await submit();

        await waitFor(() => expect(client.create).toHaveBeenCalledWith('personal', expect.any(Object)));
        expect(client.create).not.toHaveBeenCalledWith('shared', expect.anything());
    });

    it('Deep Work toggle with no project resolves a project before calling clarify', async () => {
        useAuthStore.setState({ user: { features: { ENABLE_ENGINE_DEEP_WORK: true } } } as never);
        mockFetchProjects.mockResolvedValue(list([]));
        mockCreateProject.mockResolvedValue(makeProject({ id: 'personal', name: 'Personal' }));
        mockClarifyEngine.mockResolvedValue({ action: 'proceed', questions: null, enriched_query: null, session_id: 'dw', conversation_id: 'conv' });
        mockPlanEngine.mockResolvedValue({ title: 'T', steps: [], raw_plan: {}, session_id: 'dw', conversation_id: 'conv' });
        const client = fakeClient();
        mount(client);

        fireEvent.click(screen.getByText('Deep Work'));
        fireEvent.change(box(), { target: { value: 'battery market' } });
        await act(async () => {
            fireEvent.keyDown(box(), { key: 'Enter' });
        });

        await waitFor(() => expect(mockClarifyEngine).toHaveBeenCalledWith(expect.objectContaining({ query: 'battery market', projectId: 'personal' })));
        expect(client.create).not.toHaveBeenCalled();
    });
});
