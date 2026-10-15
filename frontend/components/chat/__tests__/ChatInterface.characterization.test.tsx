/**
 * Characterization tests for the legacy chat (plan 2026-10-15-001, U4).
 *
 * These pin the observable behavior of `ChatInterface` BEFORE it is split into
 * ChatPage / LegacyChat / ChatShell / MessageList / Composer / ChatSelections.
 * They must pass unchanged on both sides of the pure move.
 */
import { TextEncoder, TextDecoder } from 'util';
(global as any).TextEncoder ??= TextEncoder;
(global as any).TextDecoder ??= TextDecoder;

import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ESM-only markdown stack: render children as plain text.
jest.mock('react-markdown', () => ({
    __esModule: true,
    default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock('remark-gfm', () => ({ __esModule: true, default: () => {} }));

jest.mock('@/components/ui/GenerativeO', () => ({
    GenerativeO: () => <div data-testid="generative-o" />,
    GenerativeOText: () => <span data-testid="generative-o-text" />,
}));

jest.mock('@/components/chat/SkillPicker', () => ({
    SkillPicker: () => null,
}));

jest.mock('@/hooks/useChatSessions', () => ({
    useChatSessionDetail: () => ({ data: undefined }),
}));
jest.mock('@/lib/api/chat-sessions', () => ({
    createChatSession: jest.fn().mockResolvedValue({ id: 'backend-session-1' }),
}));
jest.mock('@/hooks/useAgentInfo', () => ({
    useAgentInfo: () => null,
}));
jest.mock('@/lib/stores/projects', () => ({
    useProjectsStore: (selector: any) =>
        selector({ getProject: () => undefined, loadProject: jest.fn() }),
}));
jest.mock('@/hooks/useDeepWorkChat', () => ({
    useDeepWorkChat: () => ({
        deepWorkStatus: 'idle',
        isDeepWorkActive: false,
        isDeepWorkForThisChat: false,
        currentActivity: null,
        resultMessage: null,
    }),
}));

import { ChatInterface } from '../ChatInterface';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';

const enc = new TextEncoder();

function sseBody(events: Array<{ event: string; data: unknown }>) {
    const chunks = events.map(
        (e, i) => enc.encode(`id: ${i + 1}\nevent: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`),
    );
    let i = 0;
    return {
        getReader: () => ({
            read: async () =>
                i < chunks.length ? { done: false, value: chunks[i++] } : { done: true, value: undefined },
            cancel: async () => {},
            releaseLock: () => {},
        }),
    };
}

function renderChat() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <ChatInterface />
        </QueryClientProvider>,
    );
}

function chatCalls(fetchMock: jest.Mock) {
    return fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/v1/chat'));
}

async function typeAndSend(text: string) {
    const box = await screen.findByPlaceholderText('How can I help you today');
    fireEvent.change(box, { target: { value: text } });
    fireEvent.keyDown(box, { key: 'Enter', code: 'Enter' });
}

describe('ChatInterface (legacy chat) characterization', () => {
    let fetchMock: jest.Mock;
    let chatResponses: any[];

    beforeEach(() => {
        window.matchMedia = ((q: string) => ({
            matches: false, media: q, onchange: null,
            addListener: () => {}, removeListener: () => {},
            addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
        })) as any;
        localStorage.clear();
        useChatSessionsStore.setState({ sessions: [], currentSessionId: null, activeSources: {} } as any);
        // Route by URL so unrelated mount-time fetches never consume the queued chat response.
        chatResponses = [];
        fetchMock = jest.fn(async (url: any) => {
            if (String(url).endsWith('/api/v1/chat')) {
                return chatResponses.shift() ?? { ok: false, status: 500, text: async () => 'no queued response' };
            }
            return { ok: true, status: 200, text: async () => '', json: async () => ({}) };
        });
        (global as any).fetch = fetchMock;
    });

    it('renders the welcome state with an empty composer and no header actions', async () => {
        renderChat();
        expect(await screen.findByText('How can I help?')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('How can I help you today')).toHaveValue('');
        expect(screen.queryByText('Share Chat')).not.toBeInTheDocument();
    });

    it('send flow: POSTs /api/v1/chat with the legacy body and renders the streamed reply', async () => {
        chatResponses.push({
            ok: true,
            status: 200,
            body: sseBody([
                { event: 'text_delta', data: { delta: 'Hello, ', partIndex: 0 } },
                { event: 'text_delta', data: { delta: 'world.', partIndex: 0 } },
                { event: 'done', data: { messageId: 'm1', model: 'test-model' } },
            ]),
        });
        renderChat();
        await typeAndSend('What is the contract?');

        await waitFor(() => expect(chatCalls(fetchMock)).toHaveLength(1));
        const [url, init] = chatCalls(fetchMock)[0];
        expect(String(url)).toMatch(/\/api\/v1\/chat$/);
        expect(init.method).toBe('POST');
        expect(init.credentials).toBe('include');
        expect(init.headers['Content-Type']).toBe('application/json');
        const body = JSON.parse(init.body);
        expect(body).toMatchObject({
            messages: [{ role: 'user', content: 'What is the contract?' }],
            enhancedMode: true,
            streamMode: 'hybrid',
        });
        expect(typeof body.sessionId).toBe('string');
        expect(typeof body.agentId).toBe('string');
        expect(body).not.toHaveProperty('projectId');
        expect(body).not.toHaveProperty('web_search_enabled');

        expect(await screen.findByText('What is the contract?')).toBeInTheDocument();
        expect(await screen.findByText(/Hello,\s*world\./)).toBeInTheDocument();
        // After a message exists the header actions appear.
        expect(screen.getByText('Share Chat')).toBeInTheDocument();

        // Persisted to the local session store.
        await waitFor(() => {
            const s = useChatSessionsStore.getState();
            const session = s.sessions.find((x) => x.id === body.sessionId);
            expect(session?.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
            expect(session?.messages[1].content).toBe('Hello, world.');
        });
    });

    it('error path: HTTP failure shows an error and keeps the user message', async () => {
        chatResponses.push({ ok: false, status: 500, text: async () => 'boom' });
        renderChat();
        await typeAndSend('Will this fail?');

        expect(await screen.findByText(/HTTP 500: boom/)).toBeInTheDocument();
        expect(screen.getByText('Will this fail?')).toBeInTheDocument();
        // No assistant placeholder was left behind.
        const s = useChatSessionsStore.getState();
        expect(s.sessions[0].messages.map((m) => m.role)).toEqual(['user']);
    });

    it('error path: an SSE error event surfaces the message and drops the placeholder', async () => {
        chatResponses.push({
            ok: true,
            status: 200,
            body: sseBody([
                { event: 'text_delta', data: { delta: 'partial', partIndex: 0 } },
                { event: 'error', data: { message: 'model exploded' } },
            ]),
        });
        renderChat();
        await typeAndSend('Trigger stream error');

        expect(await screen.findByText(/model exploded/)).toBeInTheDocument();
        expect(screen.getByText('Trigger stream error')).toBeInTheDocument();
    });

    it('session switching: shows the stored messages of the selected session', async () => {
        const now = new Date();
        const mk = (id: string, text: string) => ({
            id,
            title: id,
            agentId: null,
            projectId: null,
            activeConnectorIds: [],
            createdAt: now,
            updatedAt: now,
            messages: [
                { id: `${id}-u`, role: 'user' as const, content: `${text} question`, timestamp: now },
                { id: `${id}-a`, role: 'assistant' as const, content: `${text} answer`, timestamp: now },
            ],
        });
        useChatSessionsStore.setState({
            sessions: [mk('s1', 'alpha'), mk('s2', 'beta')],
            currentSessionId: 's1',
        } as any);

        renderChat();
        expect(await screen.findByText('alpha question')).toBeInTheDocument();
        expect(screen.getByText('alpha answer')).toBeInTheDocument();

        act(() => useChatSessionsStore.getState().setCurrentSession('s2'));
        expect(await screen.findByText('beta question')).toBeInTheDocument();
        expect(screen.queryByText('alpha question')).not.toBeInTheDocument();

        // Switching back reloads the first session's messages.
        act(() => useChatSessionsStore.getState().setCurrentSession('s1'));
        expect(await screen.findByText('alpha question')).toBeInTheDocument();
        expect(screen.queryByText('beta question')).not.toBeInTheDocument();
    });
});
