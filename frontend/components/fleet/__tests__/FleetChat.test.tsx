import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FleetChat } from '../FleetChat';
import { useFleetJobStream, type FleetJobStreamState } from '@/hooks/useFleetJobStream';
import { interveneInRun } from '@/lib/api/fleet';
import { cancelJob } from '@/lib/api/background-jobs';
import type { RunNode } from '@/lib/run/tree';

jest.mock('@/hooks/useFleetJobStream', () => ({ useFleetJobStream: jest.fn() }));
jest.mock('@/lib/api/fleet', () => ({ interveneInRun: jest.fn() }));
jest.mock('@/lib/api/background-jobs', () => ({ cancelJob: jest.fn() }));
jest.mock('@/components/chat/shared/ChainOfThought', () => ({ ChainOfThought: () => <div data-testid="cot" /> }));
jest.mock('@/components/chat/shared/ChatMessage', () => ({
  ChatMessage: ({ message }: { message: { content: string } }) => <div data-testid="msg">{message.content}</div>,
}));

const mockHook = useFleetJobStream as jest.Mock;
const mockIntervene = interveneInRun as jest.Mock;
const mockCancel = cancelJob as jest.Mock;

const node = (over: Partial<RunNode> = {}): RunNode => ({ id: 'run:r1', kind: 'run', label: 'Run', status: 'running', startedAt: '2026-01-01T00:00:00.000Z', endedAt: null, children: [], ...over });
const state = (over: Partial<FleetJobStreamState> = {}): FleetJobStreamState => ({
  parts: [], toolCallMeta: new Map(), messages: [], tree: [], inbox: null, engine: true, connection: 'live', status: 'running',
  job: null, isLive: true, loading: false, error: null, refresh: jest.fn(async () => {}), cancelSubmission: jest.fn(async () => {}), ...over,
});

beforeEach(() => {
  jest.clearAllMocks();
  mockIntervene.mockResolvedValue({ message: 'x', sender: 's', timestamp: 't' });
  mockCancel.mockResolvedValue({ success: true, job_id: 'j', status: 'cancelled', message: 'ok' });
});

const renderChat = (over: Partial<FleetJobStreamState> = {}, conversationId: string | null = 'conv-1') => {
  mockHook.mockReturnValue(state(over));
  return render(<FleetChat runId="job-1" agentName="Scout" status="running" conversationId={conversationId} />);
};

describe('FleetChat', () => {
  it('opens the engine watch for an engine job and renders the run tree and transcript', () => {
    renderChat({
      tree: [node({ children: [node({ id: 's1', kind: 'subagent', label: 'Subagent: web_search', children: [] })] })],
      messages: [{ id: 'm1', role: 'user', content: 'go', isStreaming: false, entryIds: [], createdAt: 1 }, { id: 'm2', role: 'assistant', content: 'ok', richParts: [{ type: 'text', content: 'ok' } as never], isStreaming: false, entryIds: [], createdAt: 2 }],
    });
    expect(mockHook).toHaveBeenCalledWith('job-1', { conversationId: 'conv-1' });
    expect(screen.getByRole('tree')).toBeInTheDocument();
    expect(screen.getByTestId('cot')).toBeInTheDocument();
    expect(screen.getByText('go')).toBeInTheDocument();
  });

  it('a legacy job renders the trace parts and no run tree', () => {
    renderChat({ engine: false, parts: [{ type: 'text', content: 'trace' } as never] }, null);
    expect(mockHook).toHaveBeenCalledWith('job-1', { conversationId: null });
    expect(screen.queryByRole('tree')).toBeNull();
    expect(screen.getByTestId('cot')).toBeInTheDocument();
  });

  it('intervening sends through the fleet endpoint and shows the queued steer chip from the inbox', async () => {
    renderChat({ inbox: { queue: [{ submissionId: 'sub-1', mode: 'steer', preview: 'focus EU' }], placed: [] } });
    expect(screen.getByTestId('queued-chips')).toHaveTextContent('focus EU');
    fireEvent.change(screen.getByLabelText('Message to agent'), { target: { value: 'be brief' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(mockIntervene).toHaveBeenCalledWith('job-1', 'be brief'));
    await waitFor(() => expect(screen.getByLabelText('Message to agent')).toHaveValue(''));
  });

  it('placed steers read as consumed chips (cleared from the queue)', () => {
    renderChat({ inbox: { queue: [], placed: [{ submissionId: 'sub-1', runId: 'r1' }] } });
    expect(screen.getByTestId('queued-chips').querySelector('[data-state="consumed"]')).not.toBeNull();
  });

  it('a failed intervention restores the text and says so', async () => {
    mockIntervene.mockRejectedValue(new Error('x'));
    renderChat();
    fireEvent.change(screen.getByLabelText('Message to agent'), { target: { value: 'be brief' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Failed to send intervention message.')).toBeInTheDocument();
    expect(screen.getByLabelText('Message to agent')).toHaveValue('be brief');
  });

  it.each(['running', 'waiting', 'queued'] as const)('Cancel is enabled while %s and cancels the job', async (status) => {
    const refresh = jest.fn(async () => {});
    renderChat({ status, refresh });
    const btn = screen.getByRole('button', { name: 'Cancel run' });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith('job-1', expect.any(String)));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it('a cancelled job shows cancelled, hides Cancel and disables the input', () => {
    renderChat({ status: 'cancelled', isLive: false });
    expect(screen.getByTestId('fleet-detail-status')).toHaveAttribute('data-status', 'cancelled');
    expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull();
    expect(screen.getByLabelText('Message to agent')).toBeDisabled();
  });

  it('a resuming run shows the resume cue', () => {
    renderChat({ status: 'resuming' });
    expect(screen.getByText(/picking the run back up/i)).toBeInTheDocument();
  });

  it('shows the reconnecting banner when the watch drops', () => {
    renderChat({ connection: 'reconnecting' });
    expect(screen.getByTestId('connection-banner')).toHaveAttribute('data-connection', 'reconnecting');
  });
});
