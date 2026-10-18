import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useTestRunWaiting, useWorkflowRunTree } from '../useWorkflowRunTree';
import { fetchWaitingNodes, fetchWorkflowLogDetail, fetchWorkflowLogs } from '@/lib/api/workflow-logs';
import { useEngineConversation } from '@/lib/engine/useEngineConversation';
import { EngineConversationClient } from '@/lib/engine/client';
import type { ConversationView, TaskView } from '@/lib/engine/types';

jest.mock('@/lib/api/workflow-logs', () => ({ fetchWorkflowLogDetail: jest.fn(), fetchWaitingNodes: jest.fn(), fetchWorkflowLogs: jest.fn() }));
jest.mock('@/lib/engine/useEngineConversation', () => ({ useEngineConversation: jest.fn() }));
jest.mock('@/lib/engine/client', () => ({ EngineConversationClient: jest.fn() }));

const mockDetail = fetchWorkflowLogDetail as jest.Mock;
const mockWaiting = fetchWaitingNodes as jest.Mock;
const mockLogs = fetchWorkflowLogs as jest.Mock;
const mockConv = useEngineConversation as jest.Mock;
const mockClient = EngineConversationClient as unknown as jest.Mock;

const T0 = '2026-01-01T00:00:00.000Z';
const usage = { requests: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: 0 };
const task = (over: Partial<TaskView>): TaskView => ({ kind: 'workflow_node', parentId: 'run', status: 'running', phase: 'x', startedAt: T0, ...over });
const view = (tasks: Record<string, TaskView>): ConversationView => ({
  entries: [], live: { runId: null, taskId: null, message: null }, inbox: { queue: [], placed: [] }, approvals: { items: [] },
  questions: { items: [] }, usage: { requests: 0, total: usage, byModel: {}, byPurpose: {} }, agent: {}, toolOutput: {}, run: { active: false }, subagents: {}, tasks,
});
const log = (over: Record<string, unknown> = {}) => ({
  id: 'ex-1', workflow_id: 'wf', workflow_version_id: null, version_number: null, status: 'running', trigger: 'manual', started_at: T0, completed_at: null,
  duration_ms: null, total_tokens: 0, nodes_executed: 1, error: null, progress: 0, input_data: null, result: null, engine_conversation_id: 'conv-1',
  execution_trace: [{ step_id: 's1', node_id: 'a', node_type: 'llm', node_name: 'Fetch', status: 'completed', started_at: T0, completed_at: T0, duration_ms: 1, input: null, output: null, error: null, token_usage: null, reasoning: null }],
  ...over,
});
const waitingNode = { nodeId: 'approve', kind: 'human_in_loop', title: 'Approve', status: 'pending', wakeAt: '2026-01-02T00:00:00.000Z', approvalId: 'ap-1', conversationId: 'conv-1', taskId: 'tool-1' };

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
const setConv = (v: ConversationView | null) => mockConv.mockImplementation(() => ({ view: v, connection: 'live', cursor: null, pending: [], error: null }));
const decide = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  mockWaiting.mockResolvedValue([]);
  mockClient.mockImplementation(() => ({ decide }));
});

it('an active engine run watches its conversation and renders the engine tree', async () => {
  mockDetail.mockResolvedValue(log());
  setConv(view({ run: task({ kind: 'workflow_run', parentId: null, label: 'ex-1' }), a: task({ label: 'a', status: 'done' }), b: task({ label: 'b' }) }));
  const { result } = renderHook(() => useWorkflowRunTree('wf', 'ex-1'), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.log).not.toBeNull());
  expect(mockConv).toHaveBeenLastCalledWith('conv-1');
  expect(result.current.source).toBe('engine');
  expect(result.current.tree[0].children.map((c) => [c.label, c.status])).toEqual([['Fetch', 'completed'], ['b', 'running']]);
});

it('a finished run reads the stored trace and idles the watch', async () => {
  mockDetail.mockResolvedValue(log({ status: 'completed', completed_at: T0 }));
  setConv(view({}));
  const { result } = renderHook(() => useWorkflowRunTree('wf', 'ex-1'), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.log).not.toBeNull());
  expect(mockConv).toHaveBeenLastCalledWith(null);
  expect(result.current.source).toBe('trace');
  expect(result.current.tree[0].children[0]).toMatchObject({ label: 'Fetch', status: 'completed' });
  expect(mockWaiting).not.toHaveBeenCalled();
});

it('a legacy running run (no conversation) uses the trace and never asks for waiting nodes', async () => {
  mockDetail.mockResolvedValue(log({ engine_conversation_id: null }));
  setConv(null);
  const { result } = renderHook(() => useWorkflowRunTree('wf', 'ex-1'), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.log).not.toBeNull());
  expect(result.current.source).toBe('trace');
  expect(mockWaiting).not.toHaveBeenCalled();
});

it('a waiting node shows its wake time and decide approves through the engine decision route, then refreshes', async () => {
  mockDetail.mockResolvedValue(log({ status: 'waiting' }));
  mockWaiting.mockResolvedValue([waitingNode]);
  decide.mockResolvedValue({});
  setConv(view({ run: task({ kind: 'workflow_run', parentId: null, status: 'waiting' }), approve: task({ label: 'approve', status: 'waiting' }) }));
  const { result } = renderHook(() => useWorkflowRunTree('wf', 'ex-1'), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.waiting.size).toBe(1));
  const [node] = result.current.tree[0].children;
  expect(node).toMatchObject({ status: 'waiting', waitUntil: '2026-01-02T00:00:00.000Z' });
  const calls = mockWaiting.mock.calls.length;
  await act(async () => { await result.current.decide(result.current.waiting.get(node.id)!, 'approve'); });
  expect(decide).toHaveBeenCalledWith('conv-1', 'tool-1', 'approve');
  expect(mockWaiting.mock.calls.length).toBeGreaterThan(calls);
});

it('a 403 from the decision route becomes the "you cannot decide this" error', async () => {
  mockDetail.mockResolvedValue(log({ status: 'waiting' }));
  mockWaiting.mockResolvedValue([waitingNode]);
  decide.mockRejectedValue({ status: 403, data: { detail: 'forbidden' } });
  setConv(view({ run: task({ kind: 'workflow_run', parentId: null, status: 'waiting' }), approve: task({ label: 'approve', status: 'waiting' }) }));
  const { result } = renderHook(() => useWorkflowRunTree('wf', 'ex-1'), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.waiting.size).toBe(1));
  const w = [...result.current.waiting.values()][0];
  await act(async () => {
    await expect(result.current.decide(w, 'deny')).rejects.toMatchObject({ message: 'You cannot decide this. Ask an admin.', retryable: false, status: 403 });
  });
});

it('a waiting-nodes failure (403/404) hides the controls instead of breaking the tree', async () => {
  mockDetail.mockResolvedValue(log({ status: 'waiting' }));
  mockWaiting.mockRejectedValue({ status: 403 });
  setConv(view({ run: task({ kind: 'workflow_run', parentId: null, status: 'waiting' }), approve: task({ label: 'approve', status: 'waiting' }) }));
  const { result } = renderHook(() => useWorkflowRunTree('wf', 'ex-1'), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.log).not.toBeNull());
  await waitFor(() => expect(mockWaiting).toHaveBeenCalled());
  expect(result.current.waiting.size).toBe(0);
  expect(result.current.tree[0].children[0].status).toBe('waiting');
});

it('an undecidable item (no task id) rejects with the not-indexed copy', async () => {
  mockDetail.mockResolvedValue(log({ status: 'waiting' }));
  setConv(null);
  const { result } = renderHook(() => useWorkflowRunTree('wf', 'ex-1'), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.log).not.toBeNull());
  await act(async () => {
    await expect(result.current.decide({ ...waitingNode, taskId: null }, 'approve')).rejects.toMatchObject({ retryable: false });
  });
  expect(decide).not.toHaveBeenCalled();
});

describe('useTestRunWaiting', () => {
  const since = Date.parse(T0);
  const latest = (over: Record<string, unknown> = {}) => ({ logs: [{ id: 'ex-9', status: 'running', started_at: T0, engine_conversation_id: 'conv-9', ...over }], next_cursor: null, limit: 1, has_more: false });

  it('finds the execution the test run just started and reads its waiting nodes', async () => {
    mockLogs.mockResolvedValue(latest());
    mockWaiting.mockResolvedValue([waitingNode]);
    const { result } = renderHook(() => useTestRunWaiting('wf', true, since), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.waiting).toHaveLength(1));
    expect(mockWaiting).toHaveBeenCalledWith('ex-9');
  });

  it('ignores an older run, a legacy run, and does nothing when not running or for a local workflow', async () => {
    mockLogs.mockResolvedValue(latest({ started_at: '2025-12-31T00:00:00.000Z' }));
    const old = renderHook(() => useTestRunWaiting('wf', true, since), { wrapper: wrapper() });
    await waitFor(() => expect(mockLogs).toHaveBeenCalled());
    mockLogs.mockResolvedValue(latest({ engine_conversation_id: null }));
    const legacy = renderHook(() => useTestRunWaiting('wf', true, since), { wrapper: wrapper() });
    const idle = renderHook(() => useTestRunWaiting('wf', false, since), { wrapper: wrapper() });
    const local = renderHook(() => useTestRunWaiting('local-1', true, since), { wrapper: wrapper() });
    await waitFor(() => expect(mockLogs.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(mockWaiting).not.toHaveBeenCalled();
    expect([old, legacy, idle, local].every((h) => h.result.current.waiting.length === 0)).toBe(true);
    expect(mockLogs).not.toHaveBeenCalledWith('local-1', expect.anything());
  });
});

it('a live run updates node badges as tasks change and a skipped branch shows skipped', async () => {
  mockDetail.mockResolvedValue(log());
  const run = (nodes?: Record<string, string>) => task({ kind: 'workflow_run', parentId: null, label: 'ex-1', ...(nodes ? { nodes } : {}) });
  setConv(view({ run: run(), b: task({ label: 'b', status: 'running' }) }));
  const { result, rerender } = renderHook(() => useWorkflowRunTree('wf', 'ex-1'), { wrapper: wrapper() });
  await waitFor(() => expect(result.current.tree).toHaveLength(1));
  const before = result.current.tree[0].children.find((c) => c.label === 'b');
  expect(before?.status).toBe('running');
  setConv(view({ run: run({ b: 'success', else: 'skipped' }), b: task({ label: 'b', status: 'done' }) }));
  rerender();
  const kids = result.current.tree[0].children;
  expect(kids.map((c) => [c.label, c.status])).toEqual([['b', 'completed'], ['else', 'skipped']]);
});
