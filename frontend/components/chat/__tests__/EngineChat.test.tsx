/**
 * U7: engine chat composer, queued chips, connection banner, rejoin and the
 * optimistic-entry merge. The engine client is a scripted fake injected through
 * `useEngineConversation(id, { client })`; frames are real SSE bytes.
 */
import { TextEncoder, TextDecoder } from 'util';
Object.assign(globalThis, { TextEncoder, TextDecoder });

import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { makeBody, sseFrame } from '@/lib/engine/__fixtures__/stream';
import type { EngineConversationClient } from '@/lib/engine';
import type { ConversationView, EntryRecord } from '@/lib/engine';

jest.mock('react-markdown', () => ({ __esModule: true, default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div> }));
jest.mock('remark-gfm', () => ({ __esModule: true, default: () => {} }));
jest.mock('@/components/ui/GenerativeO', () => ({
    GenerativeO: () => <div data-testid="generative-o" />,
    GenerativeOText: () => <span />,
}));
jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@/components/chat/ChatSelections', () => ({
    buildSelectionPopovers: () => ({ webSearchPopover: null, knowledgePopover: null }),
}));
jest.mock('@/lib/api', () => {
    class APIError extends Error {
        constructor(message: string, public status?: number) {
            super(message);
        }
    }
    return { API_BASE_URL: 'http://api.test', APIError, apiFetch: jest.fn(), attemptTokenRefresh: jest.fn(async () => true) };
});

import { EngineChat } from '../EngineChat';
import { useEngineChatPrefs } from '@/lib/stores/engine-chat-prefs';

type Body = ReturnType<typeof makeBody>;
type Fake = ReturnType<typeof fakeClient>;

function fakeClient() {
    const bodies: Body[] = [];
    let failNext: Error | null = null;
    const raw = {
        create: jest.fn(async () => ({ conversationId: 'conv-new', projectId: 'p1' })),
        watch: jest.fn(async (_id: string, _cursor: number | null, signal: AbortSignal) => {
            if (failNext) {
                const e = failNext;
                failNext = null;
                throw e;
            }
            const b = makeBody(signal);
            bodies.push(b);
            return { body: b.body } as unknown as Response;
        }),
        submit: jest.fn(async (_id: string, _input: unknown) => ({})),
        abort: jest.fn(async () => ({})),
        decide: jest.fn(),
        updateSelections: jest.fn(async () => ({})),
    };
    return { raw, client: raw as unknown as EngineConversationClient, bodies, failWatchOnce: (e: Error) => (failNext = e) };
}

const usage = { requests: 0, total: { requests: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: null }, byModel: {}, byPurpose: {} };
const viewOf = (over: Partial<ConversationView> = {}): ConversationView => ({
    entries: [],
    live: { runId: null, taskId: null, message: null },
    inbox: { queue: [], placed: [] },
    approvals: { items: [] },
    usage,
    agent: {},
    toolOutput: {},
    ...over,
});
const userEntry = (id: string, text: string, requestId: string): EntryRecord => ({
    id, conversationId: 'c1', taskId: null, kind: 'user', payload: { submissionId: `s-${id}`, content: text, requestId }, createdAt: 1,
});
const running = (over: Partial<ConversationView> = {}) =>
    viewOf({ live: { runId: 'run1', taskId: 't1', message: null }, ...over });

let cursor = 0;
async function snapshot(f: Fake, view: ConversationView, streamIndex = f.bodies.length - 1) {
    await act(async () => {
        f.bodies[streamIndex].push(sseFrame({ cursor: ++cursor }, 'snapshot').replace(/data: .*\n/, `data: ${JSON.stringify({ cursor, snapshot: view })}\n`));
    });
}
async function mount(f: Fake, props: Partial<React.ComponentProps<typeof EngineChat>> = {}) {
    const utils = render(<EngineChat projectId="p1" initialConversationId="c1" client={f.client} {...props} />);
    await waitFor(() => expect(f.bodies.length).toBeGreaterThan(0));
    return utils;
}
const box = () => screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });

beforeAll(() => {
    // jsdom has no matchMedia; the animated message parts read prefers-reduced-motion.
    window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});

beforeEach(() => {
    cursor = 0;
    localStorage.clear();
    useEngineChatPrefs.setState({ drafts: {} });
    window.history.replaceState(null, '', '/chat');
});

describe('idle composer and conversation creation', () => {
    it('first send creates the conversation with selections, puts ?c= in the URL, then submits without whenBusy', async () => {
        const f = fakeClient();
        render(<EngineChat projectId="p1" client={f.client} />);
        type('hello');
        await act(async () => {
            fireEvent.keyDown(box(), { key: 'Enter' });
        });
        await waitFor(() => expect(f.raw.submit).toHaveBeenCalledTimes(1));
        expect(f.raw.create).toHaveBeenCalledWith('p1', { web_search: false, specific_sites: [], knowledge_base_ids: [] });
        expect(window.location.search).toBe('?c=conv-new');
        const [id, input] = f.raw.submit.mock.calls[0] as [string, Record<string, unknown>];
        expect(id).toBe('conv-new');
        expect(input).toMatchObject({ text: 'hello' });
        expect(input.whenBusy).toBeUndefined();
        expect(typeof input.requestId).toBe('string');
    });

    it('a failed create keeps the text and shows an error', async () => {
        const f = fakeClient();
        f.raw.create.mockRejectedValueOnce(new Error('nope'));
        render(<EngineChat projectId="p1" client={f.client} />);
        type('hello');
        await act(async () => {
            fireEvent.keyDown(box(), { key: 'Enter' });
        });
        expect(await screen.findByText('nope')).toBeTruthy();
    });
});

describe('running composer (AE5)', () => {
    it('stays enabled; Enter and the button follow up, Ctrl+Enter and Steer now steer', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, running());
        expect(box().disabled).toBe(false);

        type('one');
        await act(async () => {
            fireEvent.keyDown(box(), { key: 'Enter' });
        });
        type('two');
        await act(async () => {
            fireEvent.keyDown(box(), { key: 'Enter', ctrlKey: true });
        });
        type('three');
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Send as follow-up' }));
        });
        type('four');
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Steer now' }));
        });
        const sent = f.raw.submit.mock.calls.map((c) => c[1] as { text: string; whenBusy?: string });
        expect(sent.map((s) => [s.text, s.whenBusy])).toEqual([
            ['one', 'followUp'],
            ['two', 'steer'],
            ['three', 'followUp'],
            ['four', 'steer'],
        ]);
        expect(new Set(sent.map((s) => (s as { requestId?: string }).requestId)).size).toBe(4);
    });

    it('Shift+Enter and Escape send nothing; Escape never stops the run', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, running());
        type('draft');
        fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true });
        fireEvent.keyDown(box(), { key: 'Escape' });
        expect(f.raw.submit).not.toHaveBeenCalled();
        expect(f.raw.abort).not.toHaveBeenCalled();
    });

    it('shows inbox chips with state, and they survive a remount with the same view', async () => {
        const f = fakeClient();
        const view = running({
            inbox: { queue: [{ submissionId: 'q1', mode: 'followUp' }, { submissionId: 'q2', mode: 'steer' }], placed: [{ submissionId: 'p1', runId: 'run1' }] },
        });
        const first = await mount(f);
        await snapshot(f, view);
        const chips = () => screen.getAllByRole('listitem').map((li) => [li.getAttribute('data-mode'), li.getAttribute('data-state')]);
        expect(chips()).toEqual([['steer', 'consumed'], ['followUp', 'queued'], ['steer', 'queued']]);
        first.unmount();

        const f2 = fakeClient();
        await mount(f2);
        await snapshot(f2, view);
        expect(chips()).toEqual([['steer', 'consumed'], ['followUp', 'queued'], ['steer', 'queued']]);
    });

    it('a busy send shows a "sending" chip until acknowledged, not a duplicate bubble', async () => {
        const f = fakeClient();
        let release!: () => void;
        f.raw.submit.mockImplementationOnce(() => new Promise((r) => (release = () => r({}))));
        await mount(f);
        await snapshot(f, running());
        type('later');
        await act(async () => {
            fireEvent.keyDown(box(), { key: 'Enter' });
        });
        expect(screen.getByTestId('queued-chips').querySelector('[data-state="sending"]')).toBeTruthy();
        expect(screen.queryByTestId('pending-message')).toBeNull();
        await act(async () => release());
        expect(screen.queryByTestId('queued-chips')).toBeNull();
    });
});

describe('run active with live cleared (tool round)', () => {
    it('shows Stop and Steer while a tool executes, then returns to Send', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, viewOf({ run: { active: true, runId: 'run1', startedAt: '2026-01-01T00:00:00.000Z' } }));
        expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy();
        type('redirect');
        expect(screen.getByRole('button', { name: 'Steer now' })).toBeTruthy();
        await snapshot(f, viewOf({ run: { active: false } }));
        expect(screen.queryByRole('button', { name: /Stop/ })).toBeNull();
        expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
    });
});

describe('stop', () => {
    it('aborts once, disables Stop, and returns to idle on the terminal view', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, running());
        const stop = screen.getByRole('button', { name: 'Stop' }) as HTMLButtonElement;
        await act(async () => {
            fireEvent.click(stop);
        });
        expect(f.raw.abort).toHaveBeenCalledTimes(1);
        const stopping = screen.getByRole('button', { name: 'Stopping' }) as HTMLButtonElement;
        expect(stopping.disabled).toBe(true);
        fireEvent.click(stopping);
        expect(f.raw.abort).toHaveBeenCalledTimes(1);

        await snapshot(f, viewOf({ entries: [userEntry('u1', 'hi', 'r1')] }));
        expect(screen.queryByRole('button', { name: /Stop/ })).toBeNull();
        expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
    });
});

describe('optimistic entries (AE2)', () => {
    it('failed submit shows Retry with the same requestId; one message after the entry arrives', async () => {
        const f = fakeClient();
        f.raw.submit.mockRejectedValueOnce(new Error('offline'));
        await mount(f);
        await snapshot(f, viewOf());
        type('ping');
        await act(async () => {
            fireEvent.keyDown(box(), { key: 'Enter' });
        });
        const bubble = await screen.findByTestId('pending-message');
        expect(bubble.getAttribute('data-status')).toBe('failed');

        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
        });
        expect(f.raw.submit).toHaveBeenCalledTimes(2);
        const ids = f.raw.submit.mock.calls.map((c) => (c[1] as { requestId: string }).requestId);
        expect(ids[0]).toBe(ids[1]);
        expect(screen.getByTestId('pending-message').getAttribute('data-status')).toBe('sending');

        await snapshot(f, viewOf({ entries: [userEntry('u1', 'ping', ids[0])] }));
        expect(screen.queryByTestId('pending-message')).toBeNull();
        expect(screen.getAllByText('ping')).toHaveLength(1);
    });
});

describe('connection banner (R12)', () => {
    it('appears when the stream drops, composer stays enabled, clears on the next frame', async () => {
        const f = fakeClient();
        await mount(f, { watcherOptions: { backoffBaseMs: 5, backoffCapMs: 5, random: () => 0.5 } });
        await snapshot(f, viewOf());
        expect(screen.queryByTestId('connection-banner')).toBeNull();

        f.failWatchOnce(new Error('network'));
        await act(async () => {
            f.bodies[0].fail();
        });
        const banner = await screen.findByTestId('connection-banner');
        expect(banner.textContent).toContain('Reconnecting');
        expect(box().disabled).toBe(false);

        await waitFor(() => expect(f.bodies.length).toBe(2));
        await snapshot(f, viewOf(), 1);
        await waitFor(() => expect(screen.queryByTestId('connection-banner')).toBeNull());
    });

    it('a live stream with an active run and no frames is stalled, distinct from reconnecting', async () => {
        const f = fakeClient();
        await mount(f, { watcherOptions: { stalledAfterMs: 40 } });
        await snapshot(f, running());
        const banner = await screen.findByTestId('connection-banner');
        expect(banner.getAttribute('data-connection')).toBe('stalled');
        expect(banner.textContent).toMatch(/has not reported progress/);
        expect(box().disabled).toBe(false);
    });

    it('a missing conversation closes the composer', async () => {
        const f = fakeClient();
        const { WatchHttpError } = jest.requireActual('@/lib/engine');
        f.failWatchOnce(new WatchHttpError(404, null));
        render(<EngineChat projectId="p1" initialConversationId="gone" client={f.client} />);
        expect((await screen.findByTestId('connection-banner')).textContent).toMatch(/not available/);
        expect(box().disabled).toBe(true);
    });
});

describe('rejoin and accessibility', () => {
    it('rejoining mid-run renders committed entries without a new submit (AE3)', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, running({
            entries: [userEntry('u1', 'question', 'r1')],
            live: { runId: 'run1', taskId: 't1', message: { role: 'assistant', provider: 'p', model: 'm', blocks: [{ type: 'text', text: 'partial answer' }] } },
        }));
        expect(await screen.findByText('question')).toBeTruthy();
        expect(screen.getByText(/partial answer/)).toBeTruthy();
        expect(f.raw.submit).not.toHaveBeenCalled();
        const log = screen.getByRole('log');
        expect(log.getAttribute('aria-live')).toBe('polite');
        expect(log.getAttribute('aria-busy')).toBe('true');
    });

    it('an unsent draft survives a remount; sent text is never written to localStorage', async () => {
        const f = fakeClient();
        const first = await mount(f);
        type('half a thought');
        first.unmount();
        const f2 = fakeClient();
        await mount(f2);
        expect(box().value).toBe('half a thought');

        await snapshot(f2, viewOf());
        type('sent message text');
        await act(async () => {
            fireEvent.keyDown(box(), { key: 'Enter' });
        });
        expect(box().value).toBe('');
        const stored = JSON.stringify(Object.entries(localStorage));
        expect(stored).not.toContain('sent message text');
        expect(stored).not.toContain('half a thought');
    });
});
