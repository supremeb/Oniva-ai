import fs from 'fs';
import path from 'path';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LogDetailView } from '../LogDetailView';
import { useWorkflowRunTree, type WorkflowRunTreeState } from '@/hooks/useWorkflowRunTree';
import { workflowTraceToRunTree } from '@/lib/run/adapters/workflowRun';
import type { WaitingNode, WorkflowLogDetail } from '@/lib/api/workflow-logs';
import type { RunNode } from '@/lib/run/tree';

jest.mock('@/hooks/useWorkflowRunTree', () => ({ useWorkflowRunTree: jest.fn() }));
const mockHook = useWorkflowRunTree as jest.Mock;

const T0 = '2026-01-01T00:00:00.000Z';
const step = (node_id: string, status: string, over: Record<string, unknown> = {}) => ({
  step_id: `s-${node_id}`, node_id, node_type: 'llm', node_name: null, status, started_at: T0, completed_at: T0, duration_ms: 5,
  input: null, output: null, error: null, token_usage: null, reasoning: null, ...over,
});
const log = (status: string, trace: unknown[] = [], over: Record<string, unknown> = {}): WorkflowLogDetail =>
  ({
    id: 'ex-1', workflow_id: 'wf', workflow_version_id: null, version_number: null, status, trigger: 'manual', started_at: T0, completed_at: null,
    duration_ms: 1500, total_tokens: 42, nodes_executed: trace.length, error: null, progress: 50, input_data: null, result: null, execution_trace: trace, ...over,
  }) as unknown as WorkflowLogDetail;
const waitingInfo: WaitingNode = { nodeId: 'approve', kind: 'human_in_loop', title: 'Approve refund', status: 'pending', wakeAt: '2026-01-02T00:00:00.000Z', approvalId: 'ap-1', conversationId: 'c', taskId: 't' };

const state = (over: Partial<WorkflowRunTreeState>): WorkflowRunTreeState => ({
  log: null, tree: [], waiting: new Map(), source: null, connection: null, isLoading: false, error: null, decide: jest.fn().mockResolvedValue(undefined), ...over,
});
const fromTrace = (l: WorkflowLogDetail) => state({ log: l, tree: workflowTraceToRunTree(l), source: 'trace' });

beforeEach(() => jest.clearAllMocks());

describe('LogDetailView', () => {
  it('shows a finished run from the stored trace as a run tree with skipped and completed nodes', () => {
    const l = log('completed', [step('a', 'completed', { node_name: 'Fetch' }), step('b', 'skipped', { node_name: 'Else branch' })], { completed_at: T0 });
    mockHook.mockReturnValue(fromTrace(l));
    render(<LogDetailView workflowId="wf" runId="ex-1" onBack={jest.fn()} />);
    expect(screen.getByTestId('log-detail-status')).toHaveAttribute('data-status', 'completed');
    expect(screen.getByRole('tree', { name: /workflow run/i })).toBeInTheDocument();
    expect(screen.getByRole('treeitem', { name: 'Fetch, Completed' })).toBeInTheDocument();
    expect(screen.getByRole('treeitem', { name: 'Else branch, Skipped' })).toBeInTheDocument();
  });

  it('a live run shows its status and a waiting node with wake time and decide controls', async () => {
    const l = log('waiting', [], { engine_conversation_id: 'c' });
    const waitingNode: RunNode = { id: 'w1', kind: 'node', label: 'Approve refund', status: 'waiting', startedAt: T0, endedAt: null, waitUntil: waitingInfo.wakeAt!, children: [] };
    const root: RunNode = { id: 'run', kind: 'run', label: 'Workflow run', status: 'waiting', startedAt: T0, endedAt: null, children: [waitingNode] };
    const s = state({ log: l, tree: [root], waiting: new Map([['w1', waitingInfo]]), source: 'engine', connection: 'live' });
    mockHook.mockReturnValue(s);
    render(<LogDetailView workflowId="wf" runId="ex-1" onBack={jest.fn()} />);
    expect(screen.getByTestId('log-detail-status')).toHaveAttribute('data-status', 'waiting');
    expect(screen.getByTestId('wake-w1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Approve Approve refund' }));
    await waitFor(() => expect(s.decide).toHaveBeenCalledWith(waitingInfo, 'approve'));
    expect(screen.getByRole('link', { name: /decide in inbox/i })).toHaveAttribute('href', '/inbox');
  });

  it('an interrupted node shows its explanation', () => {
    const l = log('running', [step('a', 'interrupted', { node_name: 'Call API' })]);
    mockHook.mockReturnValue(fromTrace(l));
    render(<LogDetailView workflowId="wf" runId="ex-1" onBack={jest.fn()} />);
    expect(screen.getByText(/interrupted by a restart/i)).toBeInTheDocument();
  });

  it('a resuming run says so', () => {
    const l = log('resuming');
    mockHook.mockReturnValue(fromTrace(l));
    render(<LogDetailView workflowId="wf" runId="ex-1" onBack={jest.fn()} />);
    expect(screen.getByTestId('log-detail-status')).toHaveAttribute('data-status', 'resuming');
  });

  it('keeps per-step input and output reachable under step details', () => {
    const l = log('completed', [step('a', 'completed', { node_name: 'Fetch', output: { text: 'hello world' } })]);
    mockHook.mockReturnValue(fromTrace(l));
    render(<LogDetailView workflowId="wf" runId="ex-1" onBack={jest.fn()} />);
    expect(screen.queryByText(/hello world/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /step details/i }));
    fireEvent.click(screen.getByRole('button', { name: /Fetch/ }));
    expect(screen.getByText(/hello world/)).toBeInTheDocument();
  });

  it('shows an error and a back button when the log cannot be loaded', () => {
    const onBack = jest.fn();
    mockHook.mockReturnValue(state({ error: new Error('nope') }));
    render(<LogDetailView workflowId="wf" runId="ex-1" onBack={onBack} />);
    expect(screen.getByText(/failed to load log details/i)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: /back/i })[0]);
    expect(onBack).toHaveBeenCalled();
  });

  it('shows a loading skeleton while loading', () => {
    mockHook.mockReturnValue(state({ isLoading: true }));
    const { container } = render(<LogDetailView workflowId="wf" runId="ex-1" onBack={jest.fn()} />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeTruthy();
  });

  it('shows the run error', () => {
    const l = log('failed', [step('a', 'failed', { error: 'boom' })], { error: 'Run failed: boom' });
    mockHook.mockReturnValue(fromTrace(l));
    render(<LogDetailView workflowId="wf" runId="ex-1" onBack={jest.fn()} />);
    expect(screen.getByText('Run failed: boom')).toBeInTheDocument();
  });
});

describe('the removed recover/replay/checkpoint endpoints', () => {
  const root = path.resolve(__dirname, '../../..');
  const files = [
    'lib/api/workflows.ts',
    'lib/api/workflow-logs.ts',
    'hooks/useWorkflowLogs.ts',
    'hooks/useWorkflowRunTree.ts',
    'hooks/useWorkflow.ts',
    'components/workflow/LogsPanel.tsx',
    'components/workflow/LogDetailView.tsx',
    'components/workflow/TestRunPanel.tsx',
    'components/workflow/WorkflowRunTree.tsx',
    'components/workflow/FlowEditor.tsx',
  ];
  it.each(files)('%s makes no call to /recover, /replay or /checkpoints and has no Recover/Replay button', (f) => {
    const src = fs.readFileSync(path.join(root, f), 'utf8');
    expect(src).not.toMatch(/\/(recover|replay|checkpoints)\b/);
    expect(src).not.toMatch(/>\s*(Recover|Replay)\s*</);
  });
});
