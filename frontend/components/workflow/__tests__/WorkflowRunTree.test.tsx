import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorkflowRunTree } from '../WorkflowRunTree';
import { DecisionError } from '@/lib/engine/approval';
import type { WaitingNode } from '@/lib/api/workflow-logs';
import type { RunNode } from '@/lib/run/tree';

const leaf = (over: Partial<RunNode> & { id: string }): RunNode => ({ kind: 'node', label: over.id, status: 'completed', startedAt: null, endedAt: null, children: [], ...over });
const waitingInfo: WaitingNode = {
  nodeId: 'approve', kind: 'human_in_loop', title: 'Approve refund', status: 'pending', wakeAt: '2026-01-02T00:00:00.000Z',
  approvalId: 'ap-1', conversationId: 'conv-1', taskId: 'tool-1',
};
const tree = (): RunNode[] => [
  leaf({
    id: 'run', kind: 'run', label: 'Workflow run', status: 'waiting',
    children: [
      leaf({ id: 'a', label: 'Fetch' }),
      leaf({ id: 'w1', label: 'Approve refund', status: 'waiting', waitUntil: '2026-01-02T00:00:00.000Z' }),
      leaf({ id: 'skip', label: 'Reject path', status: 'skipped' }),
    ],
  }),
];
const map = () => new Map([['w1', waitingInfo]]);

describe('WorkflowRunTree', () => {
  it('shows the waiting node with its wake time, a link to the inbox, and approve/reject controls', () => {
    render(<WorkflowRunTree tree={tree()} waiting={map()} onDecide={jest.fn()} />);
    const item = screen.getByRole('treeitem', { name: /Approve refund, Waiting/ });
    expect(item).toHaveAttribute('data-status', 'waiting');
    expect(screen.getByTestId('wake-w1')).toHaveTextContent(/Wakes/);
    expect(screen.getByRole('link', { name: /decide in inbox/i })).toHaveAttribute('href', '/inbox');
    expect(screen.getByRole('button', { name: /^Approve Approve refund$/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /^Reject Approve refund$/ })).toBeEnabled();
  });

  it('shows skipped as skipped and gives no controls to nodes that are not waiting', () => {
    render(<WorkflowRunTree tree={tree()} waiting={map()} onDecide={jest.fn()} />);
    expect(screen.getByRole('treeitem', { name: /Reject path, Skipped/ })).toHaveAttribute('data-status', 'skipped');
    expect(screen.getAllByRole('button', { name: /^Approve /i })).toHaveLength(1);
  });

  it('approve calls onDecide with the waiting node and shows a sent state', async () => {
    const onDecide = jest.fn().mockResolvedValue(undefined);
    render(<WorkflowRunTree tree={tree()} waiting={map()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /^Approve Approve refund$/ }));
    expect(onDecide).toHaveBeenCalledWith(waitingInfo, 'approve');
    await waitFor(() => expect(screen.getByText(/decision sent/i)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /^Approve Approve refund$/ })).not.toBeInTheDocument();
  });

  it('reject sends deny', async () => {
    const onDecide = jest.fn().mockResolvedValue(undefined);
    render(<WorkflowRunTree tree={tree()} waiting={map()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /^Reject Approve refund$/ }));
    expect(onDecide).toHaveBeenCalledWith(waitingInfo, 'deny');
  });

  it('a 403 shows the cannot-decide state and locks the controls (no retry)', async () => {
    const onDecide = jest.fn().mockRejectedValue(new DecisionError('You cannot decide this. Ask an admin.', false, 403));
    render(<WorkflowRunTree tree={tree()} waiting={map()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /^Approve Approve refund$/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('You cannot decide this. Ask an admin.');
    expect(screen.getByRole('button', { name: /^Approve Approve refund$/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Reject Approve refund$/ })).toBeDisabled();
    expect(screen.getByRole('link', { name: /decide in inbox/i })).toBeInTheDocument();
  });

  it('a retryable failure shows the message and keeps the controls usable', async () => {
    const onDecide = jest.fn().mockRejectedValue(new DecisionError('Could not send your decision. Try again.', true, 500));
    render(<WorkflowRunTree tree={tree()} waiting={map()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole('button', { name: /^Approve Approve refund$/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again');
    expect(screen.getByRole('button', { name: /^Approve Approve refund$/ })).toBeEnabled();
  });

  it('double clicks send one decision while it is in flight', async () => {
    let resolve!: () => void;
    const onDecide = jest.fn().mockReturnValue(new Promise<void>((r) => (resolve = r)));
    render(<WorkflowRunTree tree={tree()} waiting={map()} onDecide={onDecide} />);
    const btn = screen.getByRole('button', { name: /^Approve Approve refund$/ });
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(onDecide).toHaveBeenCalledTimes(1);
    resolve();
    await waitFor(() => expect(screen.getByText(/decision sent/i)).toBeInTheDocument());
  });

  it('an open-ended wait shows no wake time but keeps the controls', () => {
    const t = tree();
    t[0].children[1] = { ...t[0].children[1], waitUntil: undefined };
    render(<WorkflowRunTree tree={t} waiting={map()} onDecide={jest.fn()} />);
    expect(screen.queryByTestId('wake-w1')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Approve Approve refund$/ })).toBeInTheDocument();
  });

  it('a large fan-out is paged: 1000 lanes render the first 20 with a show more control', () => {
    const lanes = Array.from({ length: 1000 }, (_, i) => leaf({ id: `lane${i}`, label: `fan#${i}` }));
    render(<WorkflowRunTree tree={[leaf({ id: 'run', kind: 'run', label: 'Workflow run', children: [leaf({ id: 'fan', label: 'fan', children: lanes })] })]} waiting={new Map()} onDecide={jest.fn()} />);
    expect(screen.getAllByRole('treeitem').length).toBeLessThan(30);
    expect(screen.getByRole('button', { name: /show more/i })).toBeInTheDocument();
  });
});
