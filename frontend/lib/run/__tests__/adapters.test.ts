import deepWorkViews from '@/lib/engine/__fixtures__/deep_work_views.json';
import type { ConversationView, EntryRecord, SubagentView, TaskView } from '@/lib/engine/types';
import type { BackgroundJob } from '@/lib/types/chat-panels';
import type { WorkflowLogDetail } from '@/lib/api/workflow-logs';
import { createEngineViewAdapter, engineViewToRunTree } from '../adapters/engineView';
import { backgroundJobToRunNode, workflowLogToRunNode } from '../adapters/legacy';
import type { RunNode } from '../tree';

const usage = { requests: 1, inputTokens: 10, outputTokens: 5, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: 0.01 };
const entry = (id: string, kind: string, payload: unknown = {}, createdAt = 1): EntryRecord => ({
  id, conversationId: 'c', taskId: null, kind, payload: payload as EntryRecord['payload'], createdAt,
});
const view = (over: Partial<ConversationView> = {}): ConversationView => ({
  entries: [],
  live: { runId: null, taskId: null, message: null },
  inbox: { queue: [], placed: [] },
  approvals: { items: [] },
  questions: { items: [] },
  usage: { requests: 1, total: usage, byModel: {}, byPurpose: {} },
  agent: {},
  toolOutput: {},
  run: { active: true, runId: 'r1', startedAt: '2026-01-01T00:00:00.000Z' },
  subagents: {},
  ...over,
});
const sub = (status: SubagentView['status'], recent: string[] = []): SubagentView => ({
  conversationId: 'child', submissionId: 's', status, startedAt: '2026-01-01T00:00:01.000Z', toolCalls: recent.length, recent, usage,
});
const task = (over: Partial<TaskView>): TaskView => ({
  kind: 'workflow_node', parentId: 'run', status: 'done', phase: 'finish', startedAt: '2026-01-01T00:00:00.000Z', ...over,
});
const find = (nodes: RunNode[], id: string): RunNode | undefined => {
  for (const n of nodes) {
    if (n.id === id) return n;
    const hit = find(n.children, id);
    if (hit) return hit;
  }
  return undefined;
};

describe('engineView adapter', () => {
  it('builds a root run with two subagents, one failed', () => {
    const v = view({ subagents: { t_a: sub('running', ['web_search: x']), t_b: sub('failed') } });
    const [root, ...rest] = engineViewToRunTree(v);
    expect(rest).toEqual([]);
    expect(root).toMatchObject({ id: 'run:r1', kind: 'run', status: 'running', usage: { inputTokens: 10 } });
    expect(root.children.map((c) => [c.id, c.kind, c.status])).toEqual([
      ['t_a', 'subagent', 'running'],
      ['t_b', 'subagent', 'failed'],
    ]);
    expect(root.children[0].label).toContain('web_search: x');
  });

  it('shows resuming after run_resumed, then running once progress lands', () => {
    const resumed = entry('e2', 'run_resumed', { runId: 'r1', at: 5 });
    const a = engineViewToRunTree(view({ entries: [entry('e1', 'user'), resumed] }));
    expect(a[0].status).toBe('resuming');
    const b = engineViewToRunTree(view({ entries: [entry('e1', 'user'), resumed, entry('e3', 'assistant')] }));
    expect(b[0].status).toBe('running');
  });

  it('marks an interrupted tool result as an interrupted tool leaf', () => {
    const e = entry('e9', 'tool_result', { toolCallId: 'tc1', toolName: 'send_email', status: 'interrupted' });
    const root = engineViewToRunTree(view({ entries: [e] }))[0];
    expect(root.children).toMatchObject([{ kind: 'tool', label: 'send_email', status: 'interrupted' }]);
  });

  it('maps a workflow view with a skipped branch to skipped leaves and waiting wake time', () => {
    const v = view({
      run: { active: false },
      tasks: {
        run: task({ kind: 'workflow_run', parentId: null, status: 'waiting', phase: 'join', label: 'Daily digest' }),
        n1: task({ label: 'fetch', status: 'done' }),
        n2: task({ label: 'branch A', status: 'done', phase: 'skipped' }),
        n3: task({ label: 'branch B', status: 'done', phase: 'skipped' }),
        n4: task({ label: 'approve', status: 'waiting', phase: 'wait', waitUntil: '2026-01-02T00:00:00.000Z' }),
        n5: task({ label: 'boom', status: 'faulted', error: 'bad' }),
        n6: task({ label: 'stopped', status: 'aborted' }),
      },
    });
    const [root] = engineViewToRunTree(v);
    expect(root).toMatchObject({ id: 'run', kind: 'run', label: 'Daily digest', status: 'running' });
    const by = (id: string) => find([root], id)!;
    expect(by('n1').status).toBe('completed');
    expect([by('n2'), by('n3')].map((n) => [n.status, n.children.length])).toEqual([['skipped', 0], ['skipped', 0]]);
    expect(by('n4')).toMatchObject({ status: 'waiting', waitUntil: '2026-01-02T00:00:00.000Z' });
    expect(by('n5')).toMatchObject({ status: 'failed', error: 'bad' });
    expect(by('n6').status).toBe('cancelled');
  });

  it('returns the identical tree for an unchanged view and reuses unchanged subtrees', () => {
    const adapt = createEngineViewAdapter();
    const v1 = view({ subagents: { t_a: sub('running'), t_b: sub('running') } });
    const t1 = adapt(v1);
    expect(adapt(v1)).toBe(t1);
    expect(adapt({ ...v1 })).toBe(t1);
    const t2 = adapt({ ...v1, subagents: { t_a: sub('running'), t_b: sub('done') } });
    expect(t2).not.toBe(t1);
    expect(t2[0].children[0]).toBe(t1[0].children[0]);
  });

  it('does not throw on an older engine view without tasks or subagents', () => {
    const old = view();
    delete (old as Partial<ConversationView>).tasks;
    delete (old as Partial<ConversationView>).subagents;
    expect(() => engineViewToRunTree(old)).not.toThrow();
    expect(engineViewToRunTree(old)[0].children).toEqual([]);
    expect(engineViewToRunTree(view({ run: undefined, subagents: undefined }))).toEqual([]);
  });

  it('handles every recorded Deep Work view', () => {
    const scenarios = deepWorkViews as unknown as Record<string, { points: { view: ConversationView }[] }>;
    for (const s of Object.values(scenarios)) {
      for (const p of s.points) expect(() => engineViewToRunTree(p.view)).not.toThrow();
    }
  });
});

describe('legacy adapter', () => {
  const job = (status: string, trace: BackgroundJob['execution_trace'] = []): BackgroundJob =>
    ({ id: 'j1', tenant_id: 't', user_id: 'u', job_type: 'workflow_execution', status, progress: 0, execution_trace: trace, created_at: '2026-01-01T00:00:00Z' }) as unknown as BackgroundJob;

  it('maps an error job to failed', () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    expect(backgroundJobToRunNode(job('error'))).toMatchObject({ status: 'failed', kind: 'run' });
    expect(backgroundJobToRunNode(job('pending')).status).toBe('queued');
    warn.mockRestore();
  });

  it('turns trace steps into node children and is reference-stable per row', () => {
    const j = job('completed', [
      { step_id: 's1', node_id: 'a', node_type: 'llm', status: 'completed' },
      { step_id: 's2', node_id: 'b', status: 'skipped' },
    ]);
    const n = backgroundJobToRunNode(j);
    expect(n.children.map((c) => c.status)).toEqual(['completed', 'skipped']);
    expect(backgroundJobToRunNode(j)).toBe(n);
  });

  it('maps a workflow log with tokens and a success status', () => {
    const log = {
      id: 'l1', workflow_id: 'w', workflow_version_id: null, version_number: 3, status: 'success', trigger: 'manual',
      started_at: null, completed_at: null, duration_ms: null, total_tokens: 42, nodes_executed: 0, error: 'x', progress: 1,
      input_data: null, result: null, execution_trace: [],
    } as unknown as WorkflowLogDetail;
    expect(workflowLogToRunNode(log)).toMatchObject({ status: 'completed', usage: { totalTokens: 42 }, error: 'x' });
  });
});
