import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import { TestRunPanel } from '../TestRunPanel';
import { useTestRunWaiting } from '@/hooks/useWorkflowRunTree';
import { applyStreamEvent, emptyLiveRun, type LiveRun } from '@/lib/run/workflowStream';
import type { WaitingNode } from '@/lib/api/workflow-logs';

jest.mock('@/hooks/useWorkflowRunTree', () => ({ useTestRunWaiting: jest.fn() }));
const mockWaiting = useTestRunWaiting as jest.Mock;

const nodes = [
  { id: 'start', type: 'start', position: { x: 0, y: 0 }, data: { label: 'Start', variables: [] } },
  { id: 'approve', type: 'human_in_loop', position: { x: 0, y: 0 }, data: { label: 'Approve refund' } },
] as unknown as Node[];
const live = (events: Array<Record<string, unknown>>): LiveRun => events.reduce((r, e) => applyStreamEvent(r, e as { type: string }), emptyLiveRun());
const waitingInfo: WaitingNode = { nodeId: 'approve', kind: 'human_in_loop', title: 'Approve refund', status: 'pending', wakeAt: '2026-01-02T00:00:00.000Z', approvalId: 'ap-1', conversationId: 'c', taskId: 't' };
const decide = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  mockWaiting.mockReturnValue({ waiting: [], decide });
});

async function startRun(onRun: () => Promise<never>, liveRun: LiveRun) {
  const utils = render(<TestRunPanel isOpen onClose={jest.fn()} nodes={nodes} onRun={onRun} liveRun={liveRun} workflowId="wf" />);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /start run/i })); });
  return utils;
}

describe('TestRunPanel on the run model', () => {
  it('TRACING shows the live run as a tree: completed, skipped and running nodes', async () => {
    const liveRun = live([
      { type: 'progress', node_id: 'start', status: 'running' },
      { type: 'node_complete', node_id: 'start', result: { status: 'success' } },
      { type: 'node_skipped', node_id: 'else' },
      { type: 'progress', node_id: 'approve', status: 'running' },
    ]);
    await startRun(() => new Promise(() => {}), liveRun);
    fireEvent.click(screen.getByRole('button', { name: 'TRACING' }));
    expect(screen.getByRole('treeitem', { name: 'Start, Completed' })).toBeInTheDocument();
    expect(screen.getByRole('treeitem', { name: 'else, Skipped' })).toBeInTheDocument();
    expect(screen.getByRole('treeitem', { name: 'Approve refund, Running' })).toBeInTheDocument();
  });

  it('a parked human node shows waiting with its wake time and an approve control that decides', async () => {
    mockWaiting.mockReturnValue({ waiting: [waitingInfo], decide });
    decide.mockResolvedValue(undefined);
    const liveRun = live([{ type: 'progress', node_id: 'approve', status: 'running' }]);
    await startRun(() => new Promise(() => {}), liveRun);
    expect(screen.getByRole('treeitem', { name: 'Approve refund, Waiting' })).toBeInTheDocument();
    expect(screen.getByTestId('wake-approve')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Approve Approve refund' }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith(waitingInfo, 'approve'));
  });

  it('only looks for waiting nodes while a run is going', async () => {
    render(<TestRunPanel isOpen onClose={jest.fn()} nodes={nodes} onRun={() => new Promise(() => {})} liveRun={emptyLiveRun()} workflowId="wf" />);
    expect(mockWaiting).toHaveBeenLastCalledWith('wf', false, expect.any(Number));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /start run/i })); });
    expect(mockWaiting).toHaveBeenLastCalledWith('wf', true, expect.any(Number));
  });

  it('the result keeps showing Completed or Failed', async () => {
    const onRun = jest.fn().mockResolvedValue({ status: 'failed', error: 'boom', elapsedMs: 3 });
    render(<TestRunPanel isOpen onClose={jest.fn()} nodes={nodes} onRun={onRun} liveRun={emptyLiveRun()} workflowId="wf" />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /start run/i })); });
    await waitFor(() => expect(screen.getByText('Failed')).toBeInTheDocument());
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('with nothing run yet TRACING invites a run', () => {
    render(<TestRunPanel isOpen onClose={jest.fn()} nodes={nodes} onRun={jest.fn()} liveRun={emptyLiveRun()} workflowId="wf" />);
    fireEvent.click(screen.getByRole('button', { name: 'TRACING' }));
    expect(screen.getByText(/run the workflow to see tracing/i)).toBeInTheDocument();
  });
});
