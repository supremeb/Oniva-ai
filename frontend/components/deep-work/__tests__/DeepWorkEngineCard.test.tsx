import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { DeepWorkState, DeepWorkWorker } from '@/lib/engine/deepWork';
import type { ConversationCost } from '@/lib/engine/cost';
import { DeepWorkCard } from '../DeepWorkCard';
import { DeepWorkEngineCard } from '../DeepWorkEngineCard';
import { useEngineSessionStore, type EngineDeepWorkSession } from '../engineSession';

const mockHook = jest.fn();
const mockCost = jest.fn();
const transcriptMounts = jest.fn();
const transcriptUnmounts = jest.fn();

jest.mock('@/lib/engine/useEngineDeepWork', () => ({
  useEngineDeepWork: (...a: unknown[]) => mockHook(...a),
  useWorkerTranscript: jest.fn(),
}));
jest.mock('@/lib/engine/cost', () => ({ useConversationCost: (...a: unknown[]) => mockCost(...a) }));
jest.mock('../WorkerTranscript', () => {
  const React = jest.requireActual('react');
  return {
    WorkerTranscript: ({ childConversationId }: { childConversationId: string }) => {
      React.useEffect(() => {
        transcriptMounts(childConversationId);
        return () => transcriptUnmounts(childConversationId);
      }, [childConversationId]);
      return <div data-testid="transcript-stub">{childConversationId}</div>;
    },
  };
});
jest.mock('@/components/chat/shared/data/FilePreview', () => ({
  FilePreview: ({ filename }: { filename: string }) => <div data-testid="file-preview">{filename}</div>,
}));
jest.mock('@/components/chat/shared/ChainOfThought', () => ({ ChainOfThought: () => null }));
jest.mock('react-markdown', () => ({ __esModule: true, default: ({ children }: { children: string }) => <div>{children}</div> }));

const SESSION: EngineDeepWorkSession = {
  sessionId: 'dw_1',
  conversationId: 'conv_1',
  chatId: 'chat_1',
  status: 'running',
  error: null,
  planTitle: 'Battery market',
};

const worker = (over: Partial<DeepWorkWorker>): DeepWorkWorker => ({
  worker_id: 'w1',
  sub_query: 'Who are the top suppliers?',
  status: 'researching',
  learning_count: 0,
  key: 'call_1',
  callId: 'call_1',
  taskId: 'task_1',
  childConversationId: 'child_1',
  round: 1,
  sources: [],
  startedAt: null,
  endedAt: null,
  ...over,
});

const state = (over: Partial<DeepWorkState> = {}): DeepWorkState => ({
  status: 'executing',
  phase: 'researching',
  plan: { title: 'Battery market', steps: ['a', 'b'] },
  rounds: [],
  workers: [
    worker({ key: 'call_1', worker_id: 'w1', status: 'complete', learning_count: 3 }),
    worker({ key: 'call_2', worker_id: 'w2', sub_query: 'What is the price trend?', status: 'researching', childConversationId: 'child_2', recentActions: ['web_search: lithium'] }),
  ],
  reflections: [],
  sections: [],
  report: '',
  sources: [],
  artifacts: [],
  usage: { requests: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0, workerTokens: 0 },
  resumed: false,
  recovering: false,
  error: null,
  startedAt: 1000,
  endedAt: null,
  ...over,
});

interface HookOpts {
  deepWork?: DeepWorkState;
  connection?: string;
  inbox?: { queue: unknown[]; placed: unknown[] };
  view?: object | null;
  steer?: jest.Mock;
  stop?: jest.Mock;
  submit?: jest.Mock;
}
function setHook(o: HookOpts = {}) {
  const submit = o.submit ?? jest.fn();
  mockHook.mockReturnValue({
    conversation: { error: null, cancelSubmission: jest.fn(), submit },
    view: o.view === undefined ? { inbox: o.inbox ?? { queue: [], placed: [] } } : o.view,
    deepWork: o.deepWork ?? state(),
    connection: o.connection ?? 'live',
    workspaceFiles: [],
    steer: o.steer ?? jest.fn().mockResolvedValue('req'),
    stop: o.stop ?? jest.fn().mockResolvedValue(undefined),
  });
  return { submit };
}

const COST: ConversationCost = {
  totalUsd: 0.1234,
  inputTokens: 5000,
  outputTokens: 900,
  unpriced: false,
  runs: [],
  byPurpose: [
    { purpose: 'deep_work.orchestrator', usd: 0.01, inputTokens: 500, outputTokens: 100, unpriced: false },
    { purpose: 'deep_work.worker', usd: 0.1, inputTokens: 4000, outputTokens: 700, unpriced: false },
    { purpose: 'deep_work.output', usd: 0, inputTokens: 500, outputTokens: 100, unpriced: true },
  ],
};

beforeEach(() => {
  jest.clearAllMocks();
  mockCost.mockReturnValue({ cost: null, active: true, liveTokens: { input: 1200, output: 300 }, refresh: jest.fn() });
  useEngineSessionStore.setState({ session: null, terminal: null });
});

describe('DeepWorkEngineCard', () => {
  it('rejoin renders plan progress from the view with no submit and no EventSource (AE4)', () => {
    const ES = jest.fn();
    (globalThis as unknown as { EventSource: unknown }).EventSource = ES;
    const { submit } = setHook();
    const { unmount } = render(<DeepWorkEngineCard session={SESSION} />);
    expect(screen.getByText('Battery market')).toBeInTheDocument();
    expect(screen.getAllByTestId('worker-row')).toHaveLength(2);
    expect(screen.getByText('Who are the top suppliers?')).toBeInTheDocument();
    expect(screen.getByText('web_search: lithium')).toBeInTheDocument();
    unmount();
    render(<DeepWorkEngineCard session={SESSION} />);
    expect(screen.getAllByTestId('worker-row')).toHaveLength(2);
    expect(submit).not.toHaveBeenCalled();
    expect(ES).not.toHaveBeenCalled();
    expect(mockHook).toHaveBeenCalledWith('conv_1', expect.objectContaining({ sessionId: 'dw_1' }));
  });

  it('shows "Planning workers…" before any worker row appears (R3, AE3)', () => {
    setHook({ deepWork: state({ phase: 'planning_workers', workers: [] }) });
    render(<DeepWorkEngineCard session={SESSION} />);
    expect(screen.getByTestId('deep-work-status')).toHaveTextContent('Planning workers…');
    expect(screen.getAllByText('Planning workers…').length).toBeGreaterThan(0);
    expect(screen.queryAllByTestId('worker-row')).toHaveLength(0);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('workers carry a text status cue besides colour', () => {
    setHook();
    render(<DeepWorkEngineCard session={SESSION} />);
    const rows = screen.getAllByTestId('worker-row');
    expect(rows[0]).toHaveAttribute('data-status', 'researching');
    expect(rows[0]).toHaveTextContent('Researching:');
    expect(rows[1]).toHaveTextContent('Done:');
  });

  it('a steer shows as a chip while sending and from the inbox, and clears when consumed (AE8)', async () => {
    let resolve!: () => void;
    const steer = jest.fn(() => new Promise<string>((r) => { resolve = () => r('req_1'); }));
    setHook({ steer });
    const { rerender } = render(<DeepWorkEngineCard session={SESSION} />);
    fireEvent.change(screen.getByLabelText('Steer the research'), { target: { value: 'focus on Europe' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(steer).toHaveBeenCalledWith('focus on Europe');
    expect(screen.getByText('focus on Europe').closest('li')).toHaveAttribute('data-state', 'sending');
    await act(async () => resolve());
    // acknowledged: the server inbox now carries it
    setHook({ steer, inbox: { queue: [{ submissionId: 's1', mode: 'steer', preview: 'focus on Europe' }], placed: [] } });
    rerender(<DeepWorkEngineCard session={{ ...SESSION }} />);
    expect(screen.getByText('focus on Europe').closest('li')).toHaveAttribute('data-state', 'queued');
    // consumed after the current tool round
    setHook({ steer, inbox: { queue: [], placed: [{ submissionId: 's1', runId: 'r' }] } });
    rerender(<DeepWorkEngineCard session={{ ...SESSION }} />);
    expect(screen.queryByText('focus on Europe')).not.toBeInTheDocument();
    expect(screen.getByTestId('queued-chips').querySelector('li')).toHaveAttribute('data-state', 'consumed');
  });

  it('a failed steer is shown as rejected', async () => {
    setHook({ steer: jest.fn().mockRejectedValue(new Error('nope')) });
    render(<DeepWorkEngineCard session={SESSION} />);
    fireEvent.change(screen.getByLabelText('Steer the research'), { target: { value: 'x y' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText('x y').closest('li')).toHaveAttribute('data-state', 'rejected'));
  });

  it('Stop calls stop once, shows stopping, then cancelled', async () => {
    const stop = jest.fn().mockResolvedValue(undefined);
    setHook({ stop });
    const { rerender } = render(<DeepWorkEngineCard session={SESSION} />);
    const btn = screen.getByRole('button', { name: 'Stop' });
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId('deep-work-status')).toHaveTextContent('Stopping...'));
    expect(stop).toHaveBeenCalledTimes(1);
    setHook({ stop, deepWork: state({ status: 'cancelled', phase: null }) });
    rerender(<DeepWorkEngineCard session={{ ...SESSION }} />);
    expect(screen.getByTestId('deep-work-status')).toHaveTextContent('Stopped');
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
  });

  it('a stop that fails re-enables the button', async () => {
    const stop = jest.fn().mockRejectedValue(new Error('x'));
    setHook({ stop });
    render(<DeepWorkEngineCard session={SESSION} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).not.toBeDisabled());
    expect(screen.getByTestId('deep-work-status')).not.toHaveTextContent('Stopping');
  });

  it('header shows tokens only while running, then the ledger total with a per-purpose popover (AE9)', () => {
    setHook();
    const { rerender } = render(<DeepWorkEngineCard session={SESSION} />);
    const badge = screen.getByTestId('cost-badge-total');
    expect(badge.textContent).toContain('estimate');
    expect(badge.textContent).not.toContain('$');
    expect(screen.queryByRole('button', { name: 'Cost by phase' })).not.toBeInTheDocument();

    mockCost.mockReturnValue({ cost: COST, active: false, liveTokens: null, refresh: jest.fn() });
    setHook({ deepWork: state({ status: 'completed', phase: null }) });
    rerender(<DeepWorkEngineCard session={{ ...SESSION }} />);
    expect(screen.getByTestId('cost-badge-total').textContent).toContain('$0.123');
    fireEvent.click(screen.getByRole('button', { name: 'Cost by phase' }));
    expect(screen.getByTestId('cost-popover-total')).toHaveTextContent('$0.123');
    const rows = screen.getByTestId('cost-popover').querySelectorAll('li');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('Orchestrator');
    expect(rows[1]).toHaveTextContent('Workers');
    expect(rows[1]).toHaveTextContent('$0.100');
    expect(rows[2]).toHaveTextContent('Output');
    expect(rows[2]).toHaveTextContent('n/a');
  });

  it('opening and closing a worker row opens and closes one transcript watch', () => {
    setHook();
    render(<DeepWorkEngineCard session={SESSION} />);
    expect(transcriptMounts).not.toHaveBeenCalled();
    const header = screen.getByText('What is the price trend?').closest('button') as HTMLElement;
    fireEvent.click(header);
    expect(transcriptMounts).toHaveBeenCalledTimes(1);
    expect(transcriptMounts).toHaveBeenCalledWith('child_2');
    expect(screen.getByTestId('transcript-stub')).toBeInTheDocument();
    fireEvent.click(header);
    expect(transcriptUnmounts).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('transcript-stub')).not.toBeInTheDocument();
  });

  it('shows reconnecting, the recovering text and the resumed chip (AE10)', () => {
    setHook({ connection: 'reconnecting', deepWork: state({ resumed: true, recovering: true }) });
    const { rerender } = render(<DeepWorkEngineCard session={SESSION} />);
    expect(screen.getByTestId('connection-banner')).toHaveAttribute('data-connection', 'reconnecting');
    expect(screen.getByTestId('deep-work-status')).toHaveTextContent('Resuming after restart');
    setHook({ connection: 'live', deepWork: state({ resumed: true, recovering: false }) });
    rerender(<DeepWorkEngineCard session={{ ...SESSION }} />);
    expect(screen.queryByTestId('connection-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('resumed-chip')).toBeInTheDocument();
  });

  it('completed run: report in a polite live region, artifacts, no steer input', () => {
    setHook({
      deepWork: state({
        status: 'completed',
        phase: null,
        report: '# Findings\nBody',
        startedAt: 1000,
        endedAt: 66_000,
        artifacts: [{ type: 'file', filename: 'deck.pdf' }],
      }),
    });
    render(<DeepWorkEngineCard session={SESSION} />);
    expect(screen.getByTestId('deep-work-report')).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByTestId('deep-work-report')).toHaveTextContent('Findings');
    expect(screen.getByTestId('file-preview')).toHaveTextContent('deck.pdf');
    expect(screen.getByText('1m 5s')).toBeInTheDocument();
    expect(screen.queryByLabelText('Steer the research')).not.toBeInTheDocument();
  });

  it('failed run shows the error', () => {
    setHook({ deepWork: state({ status: 'error', phase: null, error: 'sandbox exploded' }) });
    render(<DeepWorkEngineCard session={SESSION} />);
    expect(screen.getByText('sandbox exploded')).toBeInTheDocument();
  });

  it('reports a terminal state only when it watched the run end, never on rejoin', () => {
    setHook({ deepWork: state({ status: 'completed', phase: null }) });
    const { rerender, unmount } = render(<DeepWorkEngineCard session={SESSION} />);
    expect(useEngineSessionStore.getState().terminal).toBeNull();
    unmount();

    setHook();
    const r2 = render(<DeepWorkEngineCard session={SESSION} />);
    expect(useEngineSessionStore.getState().terminal).toBeNull();
    setHook({ deepWork: state({ status: 'completed', phase: null }) });
    r2.rerender(<DeepWorkEngineCard session={{ ...SESSION }} />);
    expect(useEngineSessionStore.getState().terminal).toEqual({ sessionId: 'dw_1', status: 'completed' });
    void rerender;
  });
});

describe('DeepWorkCard dispatch', () => {
  it('mounts the engine card for an engine session and not otherwise', () => {
    setHook();
    const { container } = render(<DeepWorkCard />);
    // legacy store is idle: nothing rendered, engine hook untouched
    expect(container).toBeEmptyDOMElement();
    expect(mockHook).not.toHaveBeenCalled();
    act(() => useEngineSessionStore.getState().setSession(SESSION));
    expect(screen.getByTestId('deep-work-engine-card')).toBeInTheDocument();
  });
});
