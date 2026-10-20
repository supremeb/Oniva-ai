import type { ConversationView, TaskView } from '@/lib/engine/types';
import type { WorkflowLogDetail } from '@/lib/api/workflow-logs';
import type { WaitingNode } from '@/lib/api/workflow-logs';
import { engineViewToRunTree } from '../adapters/engineView';
import {
  createWorkflowViewAdapter,
  nodeNamesOf,
  overlayWaiting,
  projectRows,
  selectWorkflowTree,
  workflowTraceToRunTree,
  workflowViewToRunTree,
} from '../adapters/workflowRun';
import type { RunNode } from '../tree';

const T0 = '2026-01-01T00:00:00.000Z';
const usage = { requests: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: 0 };
const baseView = (tasks: Record<string, TaskView>): ConversationView => ({
  entries: [],
  live: { runId: null, taskId: null, message: null },
  inbox: { queue: [], placed: [] },
  approvals: { items: [] },
  questions: { items: [] },
  usage: { requests: 0, total: usage, byModel: {}, byPurpose: {} },
  agent: {},
  toolOutput: {},
  run: { active: false },
  subagents: {},
  tasks,
});
const runTask = (over: Partial<TaskView> = {}): TaskView => ({
  kind: 'workflow_run', parentId: null, status: 'running', phase: 'dispatch', label: 'exec-1', startedAt: T0, ...over,
});
const nodeTask = (label: string, over: Partial<TaskView> = {}): TaskView => ({
  kind: 'workflow_node', parentId: 'run', status: 'done', phase: 'finish', label, startedAt: T0, endedAt: '2026-01-01T00:00:05.000Z', ...over,
});
const step = (node_id: string, status: string, over: Record<string, unknown> = {}) => ({
  step_id: `s-${node_id}`, node_id, node_type: 'llm', node_name: null, status, started_at: T0, completed_at: '2026-01-01T00:00:05.000Z',
  duration_ms: 5000, input: null, output: null, error: null, token_usage: null, reasoning: null, ...over,
});
const log = (status: string, trace: unknown[], over: Record<string, unknown> = {}): WorkflowLogDetail =>
  ({
    id: 'exec-1', workflow_id: 'wf', workflow_version_id: null, version_number: null, status, trigger: 'manual', started_at: T0,
    completed_at: status === 'running' ? null : '2026-01-01T00:00:09.000Z', duration_ms: 9000, total_tokens: 12, nodes_executed: trace.length,
    error: null, progress: 100, input_data: null, result: null, execution_trace: trace, ...over,
  }) as unknown as WorkflowLogDetail;
const waiting = (over: Partial<WaitingNode> = {}): WaitingNode => ({
  nodeId: 'approve', kind: 'human_in_loop', title: 'Approve refund', status: 'pending', wakeAt: '2026-01-02T00:00:00.000Z',
  approvalId: 'ap-1', conversationId: 'conv-1', taskId: 'tool-1', ...over,
});
const rowsOf = (t: RunNode[]) => projectRows(t);

describe('workflowViewToRunTree', () => {
  it('a run joined on children and an LLM node in awaitChild are running, not waiting', () => {
    const v = baseView({
      run: runTask({ status: 'waiting', phase: 'join' }),
      llm: nodeTask('llm', { status: 'waiting', phase: 'awaitChild', endedAt: undefined }),
    });
    const { tree, waiting: map } = workflowViewToRunTree(v, { waiting: [] });
    expect(tree[0].status).toBe('running');
    expect(tree[0].children[0].status).toBe('running');
    expect(map.size).toBe(0);
  });

  it('a node parked in awaitWake reads waiting even when the waiting-nodes route is unavailable', () => {
    const v = baseView({ run: runTask({ status: 'waiting', phase: 'join' }), approve: nodeTask('approve', { status: 'waiting', phase: 'awaitWake', endedAt: undefined }) });
    const { tree, waiting: map } = workflowViewToRunTree(v, { waiting: [] });
    expect(tree[0].children[0].status).toBe('waiting');
    expect(map.size).toBe(0);
  });

  it('the run reads waiting once every active node is parked on a person, running while any node works', () => {
    const parked = nodeTask('approve', { status: 'waiting', phase: 'awaitWake', endedAt: undefined });
    const working = nodeTask('llm', { status: 'waiting', phase: 'awaitChild', endedAt: undefined });
    const only = workflowViewToRunTree(baseView({ run: runTask({ status: 'waiting', phase: 'join' }), approve: parked }), { waiting: [waiting()] });
    expect(only.tree[0].status).toBe('waiting');
    const both = workflowViewToRunTree(baseView({ run: runTask({ status: 'waiting', phase: 'join' }), approve: parked, llm: working }), { waiting: [waiting()] });
    expect(both.tree[0].status).toBe('running');
  });

  it('builds a run root with node children labelled by node name', () => {
    const v = baseView({ run: runTask(), a: nodeTask('a', { parentId: 'run' }), b: nodeTask('b', { parentId: 'run', status: 'running', endedAt: undefined }) });
    const { tree } = workflowViewToRunTree(v, { names: { a: 'Fetch data' } });
    expect(tree).toHaveLength(1);
    expect(tree[0]).toMatchObject({ kind: 'run', label: 'Workflow run', status: 'running' });
    expect(tree[0].children.map((c) => [c.label, c.status])).toEqual([['Fetch data', 'completed'], ['b', 'running']]);
  });

  it('shows a skipped branch as skipped leaves even when the node never got a task', () => {
    const v = baseView({
      run: runTask({ nodes: { a: 'success', yes: 'success', no: 'skipped', after_no: 'blocked' } }),
      a: nodeTask('a'), yes: nodeTask('yes'),
    });
    const { tree } = workflowViewToRunTree(v);
    const byLabel = Object.fromEntries(tree[0].children.map((c) => [c.label, c.status]));
    expect(byLabel).toEqual({ a: 'completed', yes: 'completed', no: 'skipped', after_no: 'skipped' });
  });

  it('shows a waiting human node with its wake time from the waiting-nodes route and keys the decision by tree node id', () => {
    const v = baseView({ run: runTask({ status: 'waiting', phase: 'join' }), approve: nodeTask('approve', { status: 'waiting', phase: 'park', endedAt: undefined }) });
    const { tree, waiting: map } = workflowViewToRunTree(v, { waiting: [waiting()] });
    const node = tree[0].children[0];
    expect(node).toMatchObject({ status: 'waiting', waitUntil: '2026-01-02T00:00:00.000Z' });
    expect(map.get(node.id)).toMatchObject({ approvalId: 'ap-1', conversationId: 'conv-1', taskId: 'tool-1' });
  });

  it('prefers the engine deadline over the route wake time and ignores waiting entries for nodes not waiting', () => {
    const v = baseView({
      run: runTask({ status: 'waiting', phase: 'join' }),
      sleep: nodeTask('sleep', { status: 'waiting', waitUntil: '2026-01-01T01:00:00.000Z', endedAt: undefined }),
      done: nodeTask('done'),
    });
    const { tree, waiting: map } = workflowViewToRunTree(v, { waiting: [waiting({ nodeId: 'sleep', wakeAt: '2030-01-01T00:00:00.000Z' }), waiting({ nodeId: 'done', approvalId: 'ap-2' })] });
    const sleep = tree[0].children.find((c) => c.label === 'sleep')!;
    expect(sleep.waitUntil).toBe('2026-01-01T01:00:00.000Z');
    expect([...map.values()].map((w) => w.approvalId)).toEqual(['ap-1']);
  });

  it('keeps an open-ended wait without a wake time', () => {
    const v = baseView({ run: runTask({ status: 'waiting', phase: 'join' }), approve: nodeTask('approve', { status: 'waiting', endedAt: undefined }) });
    const { tree } = workflowViewToRunTree(v, { waiting: [waiting({ wakeAt: null })] });
    expect(tree[0].children[0].status).toBe('waiting');
    expect(tree[0].children[0].waitUntil).toBeUndefined();
  });

  it('a view from an older engine without tasks yields no tree and does not throw', () => {
    const v = baseView({});
    delete (v as { tasks?: unknown }).tasks;
    expect(workflowViewToRunTree(v).tree).toEqual([]);
  });

  it('1000 fan-out lanes build one bounded tree (paged by RunTree) without losing the lane statuses', () => {
    const tasks: Record<string, TaskView> = { run: runTask(), fan: nodeTask('fan', { status: 'running', endedAt: undefined }) };
    for (let i = 0; i < 1000; i++) tasks[`lane${i}`] = { ...nodeTask(`fan#${i}`, { parentId: 'fan', status: i < 900 ? 'done' : 'running' }) };
    const { tree } = workflowViewToRunTree(baseView(tasks));
    const fan = tree[0].children[0];
    expect(fan.children).toHaveLength(1000);
    expect(fan.children.filter((c) => c.status === 'running')).toHaveLength(100);
  });

  it('is reference-stable for an unchanged view and for unchanged subtrees', () => {
    const adapt = createWorkflowViewAdapter();
    const tasks = { run: runTask(), a: nodeTask('a'), b: nodeTask('b', { status: 'running', endedAt: undefined }) };
    const v1 = baseView(tasks);
    const r1 = adapt(v1, { names: {}, waiting: [] });
    expect(adapt(v1, { names: {}, waiting: [] })).toBe(r1);
    const v2 = baseView({ ...tasks, b: nodeTask('b') });
    const r2 = adapt(v2, { names: {}, waiting: [] });
    expect(r2.tree[0]).not.toBe(r1.tree[0]);
    expect(r2.tree[0].children[0]).toBe(r1.tree[0].children[0]);
  });
});

describe('workflowTraceToRunTree', () => {
  it('maps every trace status, including waiting, skipped and interrupted', () => {
    const l = log('completed', [step('a', 'completed'), step('b', 'skipped'), step('c', 'waiting'), step('d', 'interrupted'), step('e', 'failed', { error: 'boom' })]);
    const [root] = workflowTraceToRunTree(l);
    expect(root).toMatchObject({ kind: 'run', label: 'Workflow run', status: 'completed', usage: { totalTokens: 12 } });
    expect(root.children.map((c) => c.status)).toEqual(['completed', 'skipped', 'waiting', 'interrupted', 'failed']);
    expect(root.children[4].error).toBe('boom');
  });

  it('labels by node name and falls back to the node id', () => {
    const [root] = workflowTraceToRunTree(log('completed', [step('a', 'completed', { node_name: 'Fetch' }), step('b', 'completed')]));
    expect(root.children.map((c) => c.label)).toEqual(['Fetch', 'b']);
    expect(nodeNamesOf(log('completed', [step('a', 'completed', { node_name: 'Fetch' }), step('b', 'completed')]))).toEqual({ a: 'Fetch' });
  });

  it('engine traces (no step id, status success, repeated node ids per attempt) get unique ids', () => {
    const raw = { node_id: 'a', run_attempt: 0, attempt: 0, status: 'success', output: {}, tokens_used: 0 };
    const [root] = workflowTraceToRunTree(log('completed', [raw, { ...raw, attempt: 1 }, { ...raw, node_id: 'b', status: 'skipped' }]));
    expect(new Set(root.children.map((c) => c.id)).size).toBe(3);
    expect(root.children.map((c) => c.status)).toEqual(['completed', 'completed', 'skipped']);
  });

  it('is memoized per log object', () => {
    const l = log('completed', [step('a', 'completed')]);
    expect(workflowTraceToRunTree(l)).toBe(workflowTraceToRunTree(l));
  });

  it('a legacy status of error maps to failed', () => {
    expect(workflowTraceToRunTree(log('error', []))[0].status).toBe('failed');
  });
});

describe('finished run renders identically from the stored trace and the live tree', () => {
  it('same rows, labels and statuses (skipped branch included)', () => {
    const names = { a: 'Fetch', yes: 'Yes branch', no: 'No branch' };
    const view = baseView({
      run: runTask({ status: 'done', endedAt: T0, nodes: { a: 'success', yes: 'success', no: 'skipped' } }),
      a: nodeTask('a'), yes: nodeTask('yes'),
    });
    const trace = log('completed', [step('a', 'completed', { node_name: 'Fetch' }), step('yes', 'completed', { node_name: 'Yes branch' }), step('no', 'skipped', { node_name: 'No branch' })]);
    const live = workflowViewToRunTree(view, { names }).tree;
    const stored = workflowTraceToRunTree(trace);
    expect(rowsOf(live)).toEqual(rowsOf(stored));
  });
});

describe('selectWorkflowTree', () => {
  const view = baseView({ run: runTask(), a: nodeTask('a', { status: 'running', endedAt: undefined }) });
  it('an active engine run reads the engine view', () => {
    const l = log('running', [], { engine_conversation_id: 'c1' });
    const r = selectWorkflowTree({ log: l, view, waiting: [] });
    expect(r.source).toBe('engine');
    expect(r.tree[0].children[0].status).toBe('running');
  });
  it('a finished run reads the stored trace even when a view is present', () => {
    const l = log('completed', [step('a', 'completed')], { engine_conversation_id: 'c1' });
    expect(selectWorkflowTree({ log: l, view, waiting: [] }).source).toBe('trace');
  });
  it('an active run without a view yet falls back to the stored trace', () => {
    const l = log('running', [step('a', 'completed')]);
    expect(selectWorkflowTree({ log: l, view: null, waiting: [] }).source).toBe('trace');
  });
  it('an active run whose view has no workflow run task yet falls back to the trace', () => {
    const l = log('running', [step('a', 'completed')]);
    expect(selectWorkflowTree({ log: l, view: baseView({}), waiting: [] }).source).toBe('trace');
  });
});

describe('engineViewToRunTree on workflow_run summaries', () => {
  it('does not duplicate a node that has both a task and a summary entry', () => {
    const v = baseView({ run: runTask({ nodes: { a: 'success' } }), a: nodeTask('a') });
    expect(engineViewToRunTree(v)[0].children).toHaveLength(1);
  });
});

describe('overlayWaiting', () => {
  const leaf = (id: string, status: RunNode['status']): RunNode => ({ id, kind: 'node', label: id, status, startedAt: null, endedAt: null, children: [] });
  const root = (kids: RunNode[]): RunNode => ({ id: 'live-run', kind: 'run', label: 'Workflow run', status: 'running', startedAt: null, endedAt: null, children: kids });

  it('turns a running node that the route reports parked into waiting with its wake time', () => {
    const { tree, waiting: map } = overlayWaiting([root([leaf('a', 'completed'), leaf('approve', 'running')])], [waiting()]);
    expect(tree[0].children[1]).toMatchObject({ status: 'waiting', waitUntil: '2026-01-02T00:00:00.000Z' });
    expect(tree[0].children[0].status).toBe('completed');
    expect(map.get('approve')?.approvalId).toBe('ap-1');
  });

  it('leaves a settled node alone and returns the same tree when nothing matches', () => {
    const t = [root([leaf('approve', 'completed')])];
    expect(overlayWaiting(t, [waiting()]).tree).toBe(t);
    expect(overlayWaiting(t, []).tree).toBe(t);
  });
});
