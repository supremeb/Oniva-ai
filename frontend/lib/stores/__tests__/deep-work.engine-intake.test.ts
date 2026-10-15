/**
 * Deep Work intake on the engine API (flag ENABLE_ENGINE_DEEP_WORK): clarify -> plan -> approve ->
 * start calls the engine functions with the chat's project, and a started run is published to the
 * engine session store. The legacy path (no engine option) is exercised to stay on the old API.
 */
const mockClarifyEngine = jest.fn();
const mockPlanEngine = jest.fn();
const mockStartEngineRun = jest.fn();
const mockClarifyQuery = jest.fn();
const mockGenerateEnginePlan = jest.fn();
const mockStartEngine = jest.fn();

jest.mock('@/lib/engine/deepWorkApi', () => ({
  clarifyEngine: (...a: unknown[]) => mockClarifyEngine(...a),
  planEngine: (...a: unknown[]) => mockPlanEngine(...a),
  startEngineRun: (...a: unknown[]) => mockStartEngineRun(...a),
}));
jest.mock('@/lib/api/deep-work', () => ({
  executeDeepWork: jest.fn(),
  sendHumanInput: jest.fn(),
  pauseSession: jest.fn(),
  resumeSession: jest.fn(),
  stopSession: jest.fn(),
  buildEventsUrl: jest.fn(),
  getSessionStatus: jest.fn(),
  listWorkspaceFiles: jest.fn(),
  startEngine: (...a: unknown[]) => mockStartEngine(...a),
  cancelEngine: jest.fn(),
  buildEngineEventsUrl: jest.fn(() => 'http://x/stream'),
  generateEnginePlan: (...a: unknown[]) => mockGenerateEnginePlan(...a),
  clarifyQuery: (...a: unknown[]) => mockClarifyQuery(...a),
}));
jest.mock('@/lib/api', () => {
  class APIError extends Error {
    constructor(message: string, public status?: number, public data?: unknown) {
      super(message);
    }
  }
  return { APIError, apiFetch: jest.fn(), API_BASE_URL: 'http://api.test' };
});

import { APIError } from '@/lib/api';
import { useDeepWorkStore } from '../deep-work';
import { useEngineSessionStore } from '@/components/deep-work/engineSession';

const ENGINE = { engine: { projectId: 'proj-1' } };
const PLAN = { title: 'Battery market', steps: ['a', 'b'], raw_plan: { title: 'Battery market', sub_queries: [] }, session_id: 'dw_1', conversation_id: 'conv_1' };

beforeEach(() => {
  jest.clearAllMocks();
  useDeepWorkStore.getState().reset();
  useEngineSessionStore.setState({ session: null, terminal: null });
});

describe('engine intake', () => {
  it('clarify questions -> skip -> plan -> approve starts the run and publishes the engine session', async () => {
    mockClarifyEngine.mockResolvedValue({
      action: 'ask',
      questions: [{ text: 'Region?', options: ['EU', 'US'] }],
      enriched_query: null,
      session_id: 'dw_1',
      conversation_id: 'conv_1',
    });
    mockPlanEngine.mockResolvedValue(PLAN);
    mockStartEngineRun.mockResolvedValue({ session_id: 'dw_1', conversation_id: 'conv_1', status: 'started' });

    await useDeepWorkStore.getState().clarify('battery market', 'chat_1', ['kb1'], ['c1'], ENGINE);
    expect(mockClarifyEngine).toHaveBeenCalledWith({ query: 'battery market', projectId: 'proj-1', availableKbs: ['kb1'], availableConnectors: ['c1'] });
    expect(mockClarifyQuery).not.toHaveBeenCalled();
    expect(useDeepWorkStore.getState().status).toBe('clarifying');
    expect(useDeepWorkStore.getState().intakeResult?.questions?.[0].text).toBe('Region?');

    useDeepWorkStore.getState().skipClarification('battery market\n\nUser clarifications:\nRegion? → EU');
    await waitFor(() => expect(useDeepWorkStore.getState().status).toBe('awaiting_approval'));
    expect(mockPlanEngine).toHaveBeenCalledWith({
      query: 'battery market',
      enrichedQuery: 'battery market\n\nUser clarifications:\nRegion? → EU',
      sessionId: 'dw_1',
      conversationId: 'conv_1',
      projectId: 'proj-1',
    });
    expect(useDeepWorkStore.getState().plan?.title).toBe('Battery market');

    await useDeepWorkStore.getState().approveEnginePlan();
    expect(mockStartEngineRun).toHaveBeenCalledWith({
      sessionId: 'dw_1',
      query: 'battery market',
      plan: PLAN.raw_plan,
      enrichedQuery: 'battery market\n\nUser clarifications:\nRegion? → EU',
      knowledgeBaseIds: ['kb1'],
      connectorIds: ['c1'],
    });
    expect(mockStartEngine).not.toHaveBeenCalled();
    expect(useEngineSessionStore.getState().session).toEqual({
      sessionId: 'dw_1',
      conversationId: 'conv_1',
      chatId: 'chat_1',
      status: 'running',
      error: null,
      planTitle: 'Battery market',
    });
    // legacy store is cleared: no legacy card/progress bar next to the engine card
    expect(useDeepWorkStore.getState().status).toBe('idle');
    expect(useDeepWorkStore.getState().engineIntake).toBeNull();
    expect(useDeepWorkStore.getState().eventSource).toBeNull();
  });

  it('clarify "proceed" goes straight to the plan with the enriched query', async () => {
    mockClarifyEngine.mockResolvedValue({ action: 'proceed', questions: null, enriched_query: 'q+', session_id: 'dw_1', conversation_id: 'conv_1' });
    mockPlanEngine.mockResolvedValue(PLAN);
    await useDeepWorkStore.getState().clarify('q', 'chat_1', [], [], ENGINE);
    expect(mockPlanEngine).toHaveBeenCalledWith(expect.objectContaining({ enrichedQuery: 'q+', sessionId: 'dw_1', projectId: 'proj-1' }));
    expect(useDeepWorkStore.getState().status).toBe('awaiting_approval');
  });

  it('a failing clarify falls back to plan, which mints the session', async () => {
    mockClarifyEngine.mockRejectedValue(new Error('boom'));
    mockPlanEngine.mockResolvedValue(PLAN);
    await useDeepWorkStore.getState().clarify('q', 'chat_1', [], [], ENGINE);
    expect(mockPlanEngine).toHaveBeenCalledWith(expect.objectContaining({ query: 'q', projectId: 'proj-1', sessionId: undefined }));
    expect(useDeepWorkStore.getState().engineIntake?.sessionId).toBe('dw_1');
    expect(useDeepWorkStore.getState().status).toBe('awaiting_approval');
  });

  it('plan 422 shows the message and the model text, and Retry replans on the same session', async () => {
    mockClarifyEngine.mockResolvedValue({ action: 'proceed', questions: null, enriched_query: null, session_id: 'dw_1', conversation_id: 'conv_1' });
    mockPlanEngine.mockRejectedValueOnce(
      new APIError('x', 422, { detail: { message: 'The plan could not be parsed', text: 'here is a plan: ???', error: 'bad json' } }),
    );
    await useDeepWorkStore.getState().clarify('q', 'chat_1', [], [], ENGINE);
    const s = useDeepWorkStore.getState();
    expect(s.status).toBe('error');
    expect(s.error).toBe('The plan could not be parsed');
    expect(s.planFailure).toEqual({ message: 'The plan could not be parsed', text: 'here is a plan: ???' });

    mockPlanEngine.mockResolvedValueOnce(PLAN);
    await useDeepWorkStore.getState().generatePlan('q'); // what the card's Retry does
    expect(mockPlanEngine).toHaveBeenLastCalledWith(expect.objectContaining({ sessionId: 'dw_1', conversationId: 'conv_1', projectId: 'proj-1' }));
    expect(useDeepWorkStore.getState().status).toBe('awaiting_approval');
    expect(useDeepWorkStore.getState().planFailure).toBeNull();
  });

  it('a start failure (409) shows an error and publishes nothing', async () => {
    mockClarifyEngine.mockResolvedValue({ action: 'proceed', questions: null, enriched_query: null, session_id: 'dw_1', conversation_id: 'conv_1' });
    mockPlanEngine.mockResolvedValue(PLAN);
    mockStartEngineRun.mockRejectedValue(new APIError('A run is already active', 409));
    await useDeepWorkStore.getState().clarify('q', 'chat_1', [], [], ENGINE);
    await useDeepWorkStore.getState().approveEnginePlan();
    expect(useDeepWorkStore.getState().status).toBe('error');
    expect(useDeepWorkStore.getState().error).toBe('A run is already active');
    expect(useEngineSessionStore.getState().session).toBeNull();
  });

  it('a new intake replaces the previously published engine session', async () => {
    useEngineSessionStore.getState().setSession({ sessionId: 'old', conversationId: 'c', chatId: 'chat_1', status: 'completed', error: null, planTitle: null });
    mockClarifyEngine.mockResolvedValue({ action: 'ask', questions: [], enriched_query: null, session_id: 'dw_2', conversation_id: 'conv_2' });
    await useDeepWorkStore.getState().clarify('q', 'chat_1', [], [], ENGINE);
    expect(useEngineSessionStore.getState().session).toBeNull();
  });
});

describe('legacy intake (flag off)', () => {
  it('uses the legacy endpoints and never the engine API', async () => {
    mockClarifyQuery.mockResolvedValue({ action: 'proceed', questions: null, enriched_query: null });
    mockGenerateEnginePlan.mockResolvedValue({ title: 'T', steps: ['s'], raw_plan: { k: 1 } });
    mockStartEngine.mockResolvedValue({ session_id: 'legacy_1' });
    // connectEngineEvents opens an EventSource; stub it out
    useDeepWorkStore.setState({ connectEngineEvents: jest.fn() });

    await useDeepWorkStore.getState().clarify('q', 'chat_1', [], []);
    expect(mockClarifyQuery).toHaveBeenCalled();
    expect(mockClarifyEngine).not.toHaveBeenCalled();
    expect(useDeepWorkStore.getState().engineIntake).toBeNull();

    await useDeepWorkStore.getState().approveEnginePlan();
    expect(mockStartEngine).toHaveBeenCalledWith({ query: 'q', conversation_id: 'chat_1', plan: { k: 1 } });
    expect(mockStartEngineRun).not.toHaveBeenCalled();
    expect(useEngineSessionStore.getState().session).toBeNull();
    expect(useDeepWorkStore.getState().sessionId).toBe('legacy_1');
  });
});

function waitFor(fn: () => void, timeout = 1000): Promise<void> {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      try {
        fn();
        resolve();
      } catch (e) {
        if (Date.now() - start > timeout) reject(e);
        else setTimeout(tick, 5);
      }
    };
    tick();
  });
}
