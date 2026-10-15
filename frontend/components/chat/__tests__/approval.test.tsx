/**
 * U8: approval cards (plan 2026-10-15-001; AE6) and the cost wiring of U9 in the
 * engine chat. Card behaviour is tested on the component; the end-to-end paths
 * (pending card -> decision route -> resolved line, 403, expiry, per-turn and
 * header cost) run through EngineChat with a scripted engine client and real SSE bytes.
 */
import { TextEncoder, TextDecoder } from 'util';
Object.assign(globalThis, { TextEncoder, TextDecoder });

import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { makeBody, sseFrame } from '@/lib/engine/__fixtures__/stream';
import type { ConversationView, EngineConversationClient, EntryRecord } from '@/lib/engine';
import type { ConversationCost } from '@/lib/engine/cost';

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
import { ApprovalCard } from '../shared/interactive/ApprovalCard';
import { DecisionError, toDecisionError } from '@/lib/engine/approval';
import { useEngineChatPrefs } from '@/lib/stores/engine-chat-prefs';

beforeAll(() => {
    window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});

// ---------------------------------------------------------------------------
// ApprovalCard
// ---------------------------------------------------------------------------

describe('ApprovalCard', () => {
    const base = { id: 't1', title: 'Allow save_report?', items: [{ label: 'filename', value: 'report.md' }] };

    it('pending: shows the request and calls approve once; a double click sends one decision', async () => {
        let release!: () => void;
        const onApprove = jest.fn(() => new Promise<void>((r) => (release = r)));
        render(<ApprovalCard {...base} onApprove={onApprove} onReject={jest.fn()} />);
        expect(screen.getByText('Allow save_report?')).toBeTruthy();
        const approve = screen.getByRole('button', { name: /Approve/ });
        fireEvent.click(approve);
        fireEvent.click(approve);
        expect(onApprove).toHaveBeenCalledTimes(1);
        // Disabled while in flight, and still disabled after the call resolves (until the status changes).
        expect((screen.getByRole('button', { name: /Reject/ }) as HTMLButtonElement).disabled).toBe(true);
        await act(async () => release());
        expect((screen.getByRole('button', { name: /Reject/ }) as HTMLButtonElement).disabled).toBe(true);
        expect(onApprove).toHaveBeenCalledTimes(1);
    });

    it('a retryable failure shows the error and re-enables; a non-retryable one locks the card', async () => {
        const onApprove = jest.fn().mockRejectedValueOnce(new DecisionError('Could not send your decision. Try again.', true));
        const onReject = jest.fn().mockRejectedValue(new DecisionError('You cannot decide this.', false, 403));
        render(<ApprovalCard {...base} onApprove={onApprove} onReject={onReject} />);
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /Approve/ })));
        expect((await screen.findByRole('alert')).textContent).toContain('Try again');
        expect((screen.getByRole('button', { name: /Approve/ }) as HTMLButtonElement).disabled).toBe(false);
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /Reject/ })));
        expect((await screen.findByRole('alert')).textContent).toBe('You cannot decide this.');
        expect((screen.getByRole('button', { name: /Approve/ }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText('Allow save_report?')).toBeTruthy(); // the card stays
    });

    it('readOnly renders no buttons', () => {
        render(<ApprovalCard {...base} readOnly onApprove={jest.fn()} onReject={jest.fn()} />);
        expect(screen.queryByRole('button', { name: /Approve/ })).toBeNull();
        expect(screen.getByText(/Waiting for the conversation owner or an admin/)).toBeTruthy();
    });

    it('a decided request renders as a compact line when asked', () => {
        const { rerender } = render(<ApprovalCard {...base} toolName="save_report" status="approved" resolvedAsLine />);
        expect(screen.getByTestId('approval-resolved').textContent).toBe('Approved: save_report');
        rerender(<ApprovalCard {...base} toolName="save_report" status="rejected" note="too risky" resolvedAsLine />);
        expect(screen.getByTestId('approval-resolved').textContent).toBe('Denied: save_report (too risky)');
        expect(screen.queryByRole('button')).toBeNull();
    });

    it('a pending card past expiresAt renders expired, and flips when the deadline passes', () => {
        jest.useFakeTimers();
        try {
            jest.setSystemTime(1_000);
            const { rerender } = render(<ApprovalCard {...base} expiresAt={2_000} onApprove={jest.fn()} />);
            expect(screen.getByRole('button', { name: /Approve/ })).toBeTruthy();
            act(() => {
                jest.advanceTimersByTime(1_500);
            });
            expect(screen.queryByRole('button', { name: /Approve/ })).toBeNull();
            expect(screen.getByText(/Expired/)).toBeTruthy();
            rerender(<ApprovalCard {...base} expiresAt={1} onApprove={jest.fn()} />);
            expect(screen.queryByRole('button', { name: /Approve/ })).toBeNull();
        } finally {
            jest.useRealTimers();
        }
    });
});

describe('toDecisionError', () => {
    it('maps statuses to messages and retryability', () => {
        expect(toDecisionError({ status: 403 })).toMatchObject({ message: 'You cannot decide this.', retryable: false });
        expect(toDecisionError({ status: 409 })).toMatchObject({ retryable: false });
        expect(toDecisionError({ status: 503 })).toMatchObject({ retryable: true });
        expect(toDecisionError(new Error('network'))).toMatchObject({ retryable: true });
    });
});

// ---------------------------------------------------------------------------
// EngineChat harness
// ---------------------------------------------------------------------------

type Body = ReturnType<typeof makeBody>;

function fakeClient() {
    const bodies: Body[] = [];
    const raw = {
        create: jest.fn(),
        watch: jest.fn(async (_id: string, _cursor: number | null, signal: AbortSignal) => {
            const b = makeBody(signal);
            bodies.push(b);
            return { body: b.body } as unknown as Response;
        }),
        submit: jest.fn(async () => ({})),
        abort: jest.fn(async () => ({})),
        decide: jest.fn(async () => ({})),
        updateSelections: jest.fn(async () => ({})),
    };
    return { raw, client: raw as unknown as EngineConversationClient, bodies };
}
type Fake = ReturnType<typeof fakeClient>;

const usage = { requests: 0, total: { requests: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: null }, byModel: {}, byPurpose: {} };
const e = (id: string, kind: string, payload: unknown, createdAt = 1000): EntryRecord =>
    ({ id, conversationId: 'c1', taskId: null, kind, payload, createdAt }) as EntryRecord;
const approval = (over: Record<string, unknown> = {}) => ({
    id: 'appr1', toolTaskId: 'task1', runId: 'run1', toolCallId: 'call1', toolName: 'save_report',
    arguments: { filename: 'report.md', api_key: 'sk-live-123' }, reason: 'needs a human',
    requestedAt: 1, expiresAt: null, status: 'pending', decision: null, closedAt: null, ...over,
});
const viewWith = (items: unknown[], over: Partial<ConversationView> = {}): ConversationView =>
    ({
        entries: [
            e('u1', 'user', { submissionId: 's1', content: 'save it', requestId: 'r1' }),
            e('a1', 'assistant', { runId: 'run1', round: 0, message: { role: 'assistant', blocks: [{ type: 'tool_call', id: 'call1', name: 'save_report', arguments: { filename: 'report.md' } }] }, usage: {} }),
        ],
        live: { runId: 'run1', taskId: 't', message: null },
        inbox: { queue: [], placed: [] },
        approvals: { items },
        usage,
        agent: {},
        toolOutput: {},
        ...over,
    }) as unknown as ConversationView;

let cursor = 0;
async function snapshot(f: Fake, view: ConversationView) {
    await act(async () => {
        f.bodies[f.bodies.length - 1].push(sseFrame({ cursor: ++cursor }, 'snapshot').replace(/data: .*\n/, `data: ${JSON.stringify({ cursor, snapshot: view })}\n`));
    });
}
async function mount(f: Fake, props: Partial<React.ComponentProps<typeof EngineChat>> = {}) {
    render(<EngineChat projectId="p1" initialConversationId="c1" client={f.client} {...props} />);
    await waitFor(() => expect(f.bodies.length).toBeGreaterThan(0));
}

beforeEach(() => {
    cursor = 0;
    localStorage.clear();
    useEngineChatPrefs.setState({ drafts: {} });
    window.history.replaceState(null, '', '/chat');
});

describe('approval cards in engine chat (AE6)', () => {
    it('a pending approval renders a credential-scrubbed card; approve calls the route once; a patch resolving it swaps to the line', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, viewWith([approval()]));
        expect(await screen.findByText('Allow save_report?')).toBeTruthy();
        expect(screen.getByText('report.md')).toBeTruthy();
        expect(document.body.textContent).not.toContain('sk-live-123');

        const approve = screen.getByRole('button', { name: /Approve/ });
        await act(async () => {
            fireEvent.click(approve);
            fireEvent.click(approve);
        });
        expect(f.raw.decide).toHaveBeenCalledTimes(1);
        expect(f.raw.decide).toHaveBeenCalledWith('c1', 'task1', 'approve', undefined);
        expect((screen.getByRole('button', { name: /Reject/ }) as HTMLButtonElement).disabled).toBe(true);

        await snapshot(f, viewWith([approval({ status: 'approved', closedAt: 5, decision: { decision: 'approve', principal: 'u1', note: null, decidedAt: 5 } })]));
        expect(screen.getByTestId('approval-resolved').textContent).toBe('Approved: save_report');
        expect(screen.queryByRole('button', { name: /Approve/ })).toBeNull();
    });

    it('reject sends deny', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, viewWith([approval()]));
        await act(async () => fireEvent.click(await screen.findByRole('button', { name: /Reject/ })));
        expect(f.raw.decide).toHaveBeenCalledWith('c1', 'task1', 'deny', undefined);
    });

    it('a 403 keeps the card with an inline "You cannot decide this."', async () => {
        const f = fakeClient();
        f.raw.decide.mockRejectedValueOnce(Object.assign(new Error('Forbidden'), { status: 403 }));
        await mount(f);
        await snapshot(f, viewWith([approval()]));
        await act(async () => fireEvent.click(await screen.findByRole('button', { name: /Approve/ })));
        expect((await screen.findByRole('alert')).textContent).toBe('You cannot decide this.');
        expect(screen.getByText('Allow save_report?')).toBeTruthy();
        expect((screen.getByRole('button', { name: /Approve/ }) as HTMLButtonElement).disabled).toBe(true);
    });

    it('an expired approval renders the resolved expired line', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, viewWith([approval({ status: 'expired', closedAt: 9 })]));
        expect((await screen.findByTestId('approval-resolved')).textContent).toBe('Expired, not run: save_report');
    });

    it('survives a refresh: a new mount with the same snapshot shows the card', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, viewWith([approval()]));
        expect(await screen.findByRole('button', { name: /Approve/ })).toBeTruthy();
    });
});

// ---------------------------------------------------------------------------
// Cost wiring (U9)
// ---------------------------------------------------------------------------

describe('cost in engine chat', () => {
    const COST: ConversationCost = {
        totalUsd: 0.017, inputTokens: 180, outputTokens: 35, unpriced: false,
        runs: [
            { runId: 'run1', usd: 0.015, inputTokens: 150, outputTokens: 30, unpriced: false, startedAt: 'a' },
            { runId: 'run2', usd: 0, inputTokens: 30, outputTokens: 5, unpriced: true, startedAt: 'b' },
        ],
    };
    const done = (): ConversationView =>
        viewWith([], {
            live: { runId: null, taskId: null, message: null },
            entries: [
                e('u1', 'user', { submissionId: 's1', content: 'one', requestId: 'r1' }),
                e('a1', 'assistant', { runId: 'run1', round: 0, message: { role: 'assistant', blocks: [{ type: 'text', text: 'first answer' }] }, usage: {} }),
                e('u2', 'user', { submissionId: 's2', content: 'two', requestId: 'r2' }),
                e('a2', 'assistant', { runId: 'run2', round: 0, message: { role: 'assistant', blocks: [{ type: 'text', text: 'second answer' }] }, usage: {} }),
            ],
        });

    it('shows the total in the header and a caption under the last assistant message of each turn (n/a when unpriced)', async () => {
        const f = fakeClient();
        const costFetcher = jest.fn(async () => COST);
        await mount(f, { costFetcher });
        await snapshot(f, done());
        await waitFor(() => expect(screen.getAllByTestId('cost-badge-turn')).toHaveLength(2));
        const turns = screen.getAllByTestId('cost-badge-turn').map((n) => n.textContent);
        expect(turns[0]).toBe('150 in / 30 out · $0.015');
        expect(turns[1]).toContain('n/a');
        expect(screen.getByTestId('cost-badge-total').textContent).toContain('180 in / 35 out');
    });

    it('during a run the total is an estimate without dollars and the running turn has no caption', async () => {
        const f = fakeClient();
        const costFetcher = jest.fn(async () => COST);
        await mount(f, { costFetcher });
        const v = done();
        v.live = { runId: 'run2', taskId: 't', message: null };
        v.usage = { ...usage, total: { ...usage.total, inputTokens: 1200, outputTokens: 300 } };
        await snapshot(f, v);
        await waitFor(() => expect(costFetcher).toHaveBeenCalled());
        expect(screen.getByTestId('cost-badge-total').textContent).toContain('estimate');
        expect(screen.getByTestId('cost-badge-total').textContent).not.toContain('$');
        expect(screen.getAllByTestId('cost-badge-turn')).toHaveLength(1); // run1 only
    });
});
