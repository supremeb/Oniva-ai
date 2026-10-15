/**
 * Deep Work entry point in engine chat: a toggle in the input, shown only when the tenant's Deep Work
 * runs on the engine; the next send starts the engine intake with the chat's project instead of a chat turn.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TooltipProvider } from '@/components/ui/tooltip';

const mockClarifyEngine = jest.fn();
const mockPlanEngine = jest.fn();
const mockStartEngineRun = jest.fn();
jest.mock('@/lib/engine/deepWorkApi', () => ({
    clarifyEngine: (...a: unknown[]) => mockClarifyEngine(...a),
    planEngine: (...a: unknown[]) => mockPlanEngine(...a),
    startEngineRun: (...a: unknown[]) => mockStartEngineRun(...a),
}));
jest.mock('@/components/deep-work/DeepWorkEngineCard', () => ({
    DeepWorkEngineCard: ({ session }: { session: { sessionId: string } }) => <div data-testid="engine-card">{session.sessionId}</div>,
}));
jest.mock('@/components/chat/shared/ChainOfThought', () => ({ ChainOfThought: () => <div data-testid="thinking" /> }));
jest.mock('react-markdown', () => ({ __esModule: true, default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div> }));
jest.mock('remark-gfm', () => ({ __esModule: true, default: () => {} }));
jest.mock('@/components/ui/GenerativeO', () => ({ GenerativeO: () => null }));
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
import { useDeepWorkStore } from '@/lib/stores/deep-work';
import { useEngineSessionStore } from '@/components/deep-work/engineSession';
import { useEngineChatPrefs } from '@/lib/stores/engine-chat-prefs';
import type { EngineConversationClient } from '@/lib/engine';

const setFlag = (on: boolean) =>
    useAuthStore.setState({ user: { features: { ENABLE_ENGINE_DEEP_WORK: on } } } as never);
const client = { create: jest.fn(), watch: jest.fn(), submit: jest.fn(), abort: jest.fn(), updateSelections: jest.fn() };
const mount = () =>
    render(
        <TooltipProvider>
            <EngineChat projectId="p1" client={client as unknown as EngineConversationClient} />
        </TooltipProvider>
    );
const box = () => screen.getByRole('textbox', { name: 'Message' });

beforeAll(() => {
    window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});
beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    useEngineChatPrefs.setState({ drafts: {} });
    useDeepWorkStore.getState().reset();
    useEngineSessionStore.setState({ session: null, terminal: null });
    window.history.replaceState(null, '', '/chat');
});

it('flag off: no Deep Work toggle and a send is a normal chat turn', async () => {
    setFlag(false);
    client.create.mockResolvedValue({ conversationId: 'conv-new', projectId: 'p1' });
    client.watch.mockReturnValue(new Promise(() => {}));
    mount();
    expect(screen.queryByText('Deep Work')).toBeNull();
    fireEvent.change(box(), { target: { value: 'hello' } });
    await act(async () => {
        fireEvent.keyDown(box(), { key: 'Enter' });
    });
    await waitFor(() => expect(client.create).toHaveBeenCalled());
    expect(mockClarifyEngine).not.toHaveBeenCalled();
});

it('flag on: toggle + send runs the engine intake with the chat project, then the plan, start and engine card', async () => {
    setFlag(true);
    mockClarifyEngine.mockResolvedValue({ action: 'proceed', questions: null, enriched_query: null, session_id: 'dw_1', conversation_id: 'conv_1' });
    mockPlanEngine.mockResolvedValue({ title: 'Battery market', steps: ['a'], raw_plan: { title: 'Battery market' }, session_id: 'dw_1', conversation_id: 'conv_1' });
    mockStartEngineRun.mockResolvedValue({ session_id: 'dw_1', conversation_id: 'conv_1', status: 'started' });
    mount();

    fireEvent.click(screen.getByText('Deep Work'));
    fireEvent.change(box(), { target: { value: 'battery market' } });
    await act(async () => {
        fireEvent.keyDown(box(), { key: 'Enter' });
    });

    await waitFor(() => expect(mockClarifyEngine).toHaveBeenCalledWith(expect.objectContaining({ query: 'battery market', projectId: 'p1' })));
    expect(client.create).not.toHaveBeenCalled(); // not a chat turn
    expect(screen.getByTestId('deep-work-query').textContent).toBe('battery market');

    // plan approval card is the existing PlanCard
    const approve = await screen.findByRole('button', { name: /proceed/i });
    await act(async () => {
        fireEvent.click(approve);
    });
    await waitFor(() => expect(mockStartEngineRun).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'dw_1' })));
    expect((await screen.findByTestId('engine-card')).textContent).toBe('dw_1');
});
