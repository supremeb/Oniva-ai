/**
 * U8: approval cards (plan 2026-10-15-001; AE6) and the cost wiring of U9 in the
 * engine chat. Card behaviour is tested on the component; the end-to-end paths
 * (pending card -> decision route -> resolved line, 403, expiry, per-turn and
 * header cost) run through EngineChat with a scripted engine client and real SSE bytes.
 */
import { TextEncoder, TextDecoder } from 'util';
Object.assign(globalThis, { TextEncoder, TextDecoder });

import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
jest.mock('@/components/chat/EngineSkillConnectorPickers', () => ({ EngineSkillConnectorPickers: () => null }));
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
import { QuestionCard } from '../shared/interactive/QuestionCard';
import { DecisionError } from '@/lib/engine/approval';
import { useEngineChatPrefs } from '@/lib/stores/engine-chat-prefs';
import questionViews from '@/lib/engine/__fixtures__/question_views.json';

beforeAll(() => {
    window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});

const OPTIONS = [{ id: 'staging', label: 'Staging' }, { id: 'prod', label: 'Production', description: 'customer facing' }];
const base = { id: 't1', question: 'Which environments?', options: OPTIONS };
const send = () => screen.getByRole('button', { name: /Send answer/ }) as HTMLButtonElement;

describe('QuestionCard', () => {
    it('single select: choosing an option and submitting calls onAnswer once with the ids', async () => {
        let release!: () => void;
        const onAnswer = jest.fn(() => new Promise<void>((r) => (release = r)));
        render(<QuestionCard {...base} onAnswer={onAnswer} />);
        expect(send().disabled).toBe(true);
        fireEvent.click(screen.getByLabelText(/Staging/));
        fireEvent.click(screen.getByLabelText(/Production/));
        expect((screen.getByLabelText(/Staging/) as HTMLInputElement).checked).toBe(false); // radio
        fireEvent.click(send());
        fireEvent.click(send());
        expect(onAnswer).toHaveBeenCalledTimes(1);
        expect(onAnswer).toHaveBeenCalledWith({ optionIds: ['prod'] });
        expect(send().disabled).toBe(true);
        await act(async () => release());
        expect(send().disabled).toBe(true); // stays locked until the status changes
        expect(onAnswer).toHaveBeenCalledTimes(1);
    });

    it('multi select sends every chosen id', async () => {
        const onAnswer = jest.fn(async () => {});
        render(<QuestionCard {...base} multiSelect onAnswer={onAnswer} />);
        fireEvent.click(screen.getByLabelText(/Staging/));
        fireEvent.click(screen.getByLabelText(/Production/));
        await act(async () => fireEvent.click(send()));
        expect(onAnswer).toHaveBeenCalledWith({ optionIds: ['staging', 'prod'] });
    });

    it('free text is offered only when allowed, and is sent as otherValue', async () => {
        const onAnswer = jest.fn(async () => {});
        const { rerender } = render(<QuestionCard {...base} onAnswer={onAnswer} />);
        expect(screen.queryByLabelText(/Something else/)).toBeNull();
        rerender(<QuestionCard {...base} allowFreeText onAnswer={onAnswer} />);
        fireEvent.change(screen.getByLabelText(/Something else/), { target: { value: '  use the canary ' } });
        await act(async () => fireEvent.click(send()));
        expect(onAnswer).toHaveBeenCalledWith({ optionIds: [], otherValue: 'use the canary' });
    });

    it('a question without options is a text box', async () => {
        const onAnswer = jest.fn(async () => {});
        render(<QuestionCard id="t" question="Anything else?" options={[]} onAnswer={onAnswer} />);
        fireEvent.change(screen.getByLabelText(/Your answer/), { target: { value: 'no' } });
        await act(async () => fireEvent.click(send()));
        expect(onAnswer).toHaveBeenCalledWith({ optionIds: [], otherValue: 'no' });
    });

    it('a retryable failure shows the error inline and re-enables; a non-retryable one locks', async () => {
        const onAnswer = jest.fn()
            .mockRejectedValueOnce(new DecisionError('Could not send your decision. Try again.', true))
            .mockRejectedValue(new DecisionError('You cannot decide this. Ask an admin.', false, 403));
        render(<QuestionCard {...base} onAnswer={onAnswer} />);
        fireEvent.click(screen.getByLabelText(/Staging/));
        await act(async () => fireEvent.click(send()));
        expect((await screen.findByRole('alert')).textContent).toContain('Try again');
        expect(send().disabled).toBe(false);
        await act(async () => fireEvent.click(send()));
        expect((await screen.findByRole('alert')).textContent).toBe('You cannot decide this. Ask an admin.');
        expect(send().disabled).toBe(true);
        expect(screen.getByText('Which environments?')).toBeTruthy();
        expect(onAnswer).toHaveBeenCalledTimes(2);
    });

    it('answered renders read-only with the answer; expired says so; readOnly has no controls', () => {
        const { rerender } = render(
            <QuestionCard {...base} status="answered" answer={{ optionIds: ['staging'], text: 'and the canary' }} onAnswer={jest.fn()} />
        );
        expect(screen.queryByRole('button')).toBeNull();
        expect(screen.getByTestId('question-answer').textContent).toBe('Staging, and the canary');
        rerender(<QuestionCard {...base} status="expired" onAnswer={jest.fn()} />);
        expect(screen.getByText(/Expired/)).toBeTruthy();
        expect(screen.queryByRole('button')).toBeNull();
        rerender(<QuestionCard {...base} readOnly onAnswer={jest.fn()} />);
        expect(screen.queryByRole('button')).toBeNull();
        expect(screen.getByText(/Waiting for the conversation owner or an admin/)).toBeTruthy();
    });

    it('a pending card past expiresAt renders expired', () => {
        render(<QuestionCard {...base} expiresAt={1} onAnswer={jest.fn()} />);
        expect(screen.queryByRole('button')).toBeNull();
        expect(screen.getByText(/Expired/)).toBeTruthy();
    });
});

// ---------------------------------------------------------------------------
// EngineChat
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
        answer: jest.fn(async () => ({})),
        updateSelections: jest.fn(async () => ({})),
    };
    return { raw, client: raw as unknown as EngineConversationClient, bodies };
}
type Fake = ReturnType<typeof fakeClient>;

const views = questionViews as unknown as Record<string, ConversationView>;
const TASK = views.question_pending.questions.items[0].toolTaskId;

let cursor = 0;
async function snapshot(f: Fake, view: ConversationView) {
    await act(async () => {
        f.bodies[f.bodies.length - 1].push(sseFrame({ cursor: ++cursor }, 'snapshot').replace(/data: .*\n/, `data: ${JSON.stringify({ cursor, snapshot: view })}\n`));
    });
}
async function mount(f: Fake) {
    render(<EngineChat projectId="p1" initialConversationId="c1" client={f.client} />);
    await waitFor(() => expect(f.bodies.length).toBeGreaterThan(0));
}
const apiErr = (status: number, detail?: string) =>
    Object.assign(new Error('failed'), { status, data: detail ? { detail } : undefined });

// The recorded views use the engine's manual clock (requestedAt 1_000_000, expiresAt 2_800_000).
beforeEach(() => {
    jest.spyOn(Date, 'now').mockReturnValue(1_500_000);
    cursor = 0;
    localStorage.clear();
    useEngineChatPrefs.setState({ drafts: {} });
    window.history.replaceState(null, '', '/chat');
});

afterEach(() => jest.restoreAllMocks());

describe('question cards in engine chat (AE8)', () => {
    it('a recorded pending question renders a card; picking options and sending calls the route once', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, views.question_pending);
        expect(await screen.findByText('Which environments should I deploy to?')).toBeTruthy();
        fireEvent.click(screen.getByLabelText(/Staging/));
        fireEvent.change(screen.getByLabelText(/Something else/), { target: { value: 'and the canary' } });
        await act(async () => {
            fireEvent.click(send());
            fireEvent.click(send());
        });
        expect(f.raw.answer).toHaveBeenCalledTimes(1);
        expect(f.raw.answer).toHaveBeenCalledWith('c1', TASK, { optionIds: ['staging'], text: 'and the canary' });
        expect(send().disabled).toBe(true);
    });

    it('free text alone is sent as text without optionIds noise', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, views.question_pending);
        fireEvent.change(await screen.findByLabelText(/Something else/), { target: { value: 'neither' } });
        await act(async () => fireEvent.click(send()));
        expect(f.raw.answer).toHaveBeenCalledWith('c1', TASK, { optionIds: [], text: 'neither' });
    });

    it('the answered view replaces the card with the read-only answer', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, views.question_pending);
        await screen.findByRole('button', { name: /Send answer/ });
        await snapshot(f, views.question_answered);
        expect(await screen.findByTestId('question-answer')).toBeTruthy();
        expect(screen.getByTestId('question-answer').textContent).toBe('Staging, and the canary');
        expect(screen.queryByRole('button', { name: /Send answer/ })).toBeNull();
    });

    it('the expired view shows the expired card', async () => {
        const f = fakeClient();
        await mount(f);
        await snapshot(f, views.question_expired);
        expect(await screen.findByText(/Expired/)).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Send answer/ })).toBeNull();
    });

    const pickAndSend = async () => {
        fireEvent.click(await screen.findByLabelText(/Staging/));
        await act(async () => fireEvent.click(send()));
    };

    it.each([
        [403, 'not_authorized', 'You cannot decide this. Ask an admin.', true],
        [409, 'already_answered', 'This request was already decided or has expired.', true],
        [410, 'approval_expired', 'This request was already decided or has expired.', true],
    ])('%s %s shows the approval copy and locks the card', async (status, detail, message, locked) => {
        const f = fakeClient();
        f.raw.answer.mockRejectedValue(apiErr(status as number, detail as string));
        await mount(f);
        await snapshot(f, views.question_pending);
        await pickAndSend();
        expect((await screen.findByRole('alert')).textContent).toBe(message);
        expect(send().disabled).toBe(locked);
        expect(screen.getByText('Which environments should I deploy to?')).toBeTruthy();
    });

    it('502 stays retryable and a second send goes through', async () => {
        const f = fakeClient();
        f.raw.answer.mockRejectedValueOnce(apiErr(502, 'Engine unavailable'));
        await mount(f);
        await snapshot(f, views.question_pending);
        await pickAndSend();
        expect((await screen.findByRole('alert')).textContent).toBe('Could not send your decision. Try again.');
        expect(send().disabled).toBe(false);
        await act(async () => fireEvent.click(send()));
        expect(f.raw.answer).toHaveBeenCalledTimes(2);
    });

    it('a view without questions (older engine) still renders', async () => {
        const f = fakeClient();
        await mount(f);
        const v = JSON.parse(JSON.stringify(views.question_pending));
        delete v.questions;
        await snapshot(f, v);
        expect(await screen.findByText('deploy it')).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Send answer/ })).toBeNull();
    });
});
