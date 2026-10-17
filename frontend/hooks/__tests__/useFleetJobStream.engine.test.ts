import { act, renderHook } from '@testing-library/react';
import { useFleetJobStream } from '../useFleetJobStream';
import { getBackgroundJob, createJobStream } from '@/lib/api/background-jobs';
import { useEngineConversation } from '@/lib/engine/useEngineConversation';
import type { ConversationView, EntryRecord, TaskView } from '@/lib/engine/types';
import type { BackgroundJob } from '@/lib/types/chat-panels';

jest.mock('@/lib/api/background-jobs', () => ({ getBackgroundJob: jest.fn(), createJobStream: jest.fn() }));
jest.mock('@/lib/engine/useEngineConversation', () => ({ useEngineConversation: jest.fn() }));

const mockGetJob = getBackgroundJob as jest.MockedFunction<typeof getBackgroundJob>;
const mockStream = createJobStream as jest.MockedFunction<typeof createJobStream>;
const mockConv = useEngineConversation as jest.MockedFunction<typeof useEngineConversation>;

const usage = { requests: 1, inputTokens: 10, outputTokens: 5, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: 0.01 };
const entry = (id: string, kind: string, payload: unknown, createdAt = 1): EntryRecord => ({
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
const job = (over: Partial<BackgroundJob> = {}): BackgroundJob => ({
  id: 'job-1', tenant_id: 't', user_id: 'u', job_type: 'agent_chat', status: 'running', progress: 0,
  execution_trace: [], created_at: '2026-01-01T00:00:00Z', ...over,
});

let state: Record<string, unknown>;
const conv = (over: Record<string, unknown> = {}) => {
  state = { view: null, cursor: null, connection: 'live', pending: [], error: null, submit: jest.fn(), retry: jest.fn(), abort: jest.fn(), cancelSubmission: jest.fn(), decide: jest.fn(), ...over };
  mockConv.mockImplementation(() => state as never);
};
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => {
  jest.clearAllMocks();
});

describe('useFleetJobStream on the engine', () => {
  it('watches the conversation, builds the run tree and transcript, and opens no job stream', async () => {
    mockGetJob.mockResolvedValue(job());
    const v = view({
      entries: [entry('e1', 'user', { content: 'research it', requestId: 'q' })],
      subagents: { t_a: { conversationId: 'k', submissionId: 's', status: 'running', startedAt: '2026-01-01T00:00:01.000Z', toolCalls: 1, recent: ['web_search: x'], usage } },
    });
    conv({ view: v });
    const { result } = renderHook(() => useFleetJobStream('job-1', { conversationId: 'conv-1' }));
    await flush();

    expect(mockConv).toHaveBeenCalledWith('conv-1');
    expect(mockStream).not.toHaveBeenCalled();
    expect(result.current.engine).toBe(true);
    expect(result.current.loading).toBe(false);
    expect(result.current.messages.map((m) => [m.role, m.content])).toEqual([['user', 'research it']]);
    expect(result.current.tree[0]).toMatchObject({ id: 'run:r1', status: 'running' });
    expect(result.current.tree[0].children[0].kind).toBe('subagent');
    expect(result.current.status).toBe('running');
    expect(result.current.isLive).toBe(true);
  });

  it('a legacy job (no conversation id) keeps the trace path and idles the engine hook', async () => {
    conv();
    mockGetJob.mockResolvedValue(job({ status: 'running' }));
    mockStream.mockReturnValue(jest.fn());
    const { result } = renderHook(() => useFleetJobStream('job-1', { conversationId: null }));
    await flush();
    expect(mockConv).toHaveBeenCalledWith(null);
    expect(result.current.engine).toBe(false);
    expect(mockStream).toHaveBeenCalledTimes(1);
  });

  it('queued steers are exposed from the server inbox and can be withdrawn', async () => {
    mockGetJob.mockResolvedValue(job());
    const inbox = { queue: [{ submissionId: 'sub-1', mode: 'steer' as const, preview: 'focus on EU' }], placed: [] };
    conv({ view: view({ inbox }) });
    const { result } = renderHook(() => useFleetJobStream('job-1', { conversationId: 'conv-1' }));
    await flush();
    expect(result.current.inbox).toEqual(inbox);
    await result.current.cancelSubmission('sub-1');
    expect((state.cancelSubmission as jest.Mock)).toHaveBeenCalledWith('sub-1');
  });

  it('a refresh mid-run rejoins: the replayed view brings back the transcript text', async () => {
    mockGetJob.mockResolvedValue(job());
    const assistant = entry('e2', 'assistant', { runId: 'r1', round: 0, message: { role: 'assistant', provider: 'f', model: 'm', blocks: [{ type: 'text', text: 'partial findings' }] } });
    conv({ view: view({ entries: [assistant] }), connection: 'connecting' });
    const first = renderHook(() => useFleetJobStream('job-1', { conversationId: 'conv-1' }));
    await flush();
    first.unmount();
    conv({ view: view({ entries: [assistant] }) });
    const second = renderHook(() => useFleetJobStream('job-1', { conversationId: 'conv-1' }));
    await flush();
    expect(second.result.current.messages.map((m) => m.content)).toEqual(['partial findings']);
    expect(mockGetJob).toHaveBeenCalledTimes(2); // each mount reads the job row; the transcript comes from the watch
  });

  it('a resuming run is surfaced as resuming', async () => {
    mockGetJob.mockResolvedValue(job());
    const v = view({ entries: [entry('e1', 'run_resumed', {}, 5)], subagents: {} });
    conv({ view: v });
    const { result } = renderHook(() => useFleetJobStream('job-1', { conversationId: 'conv-1' }));
    await flush();
    expect(result.current.status).toBe('resuming');
  });

  it('a cancelled job row wins over a stale view and ends the live state', async () => {
    mockGetJob.mockResolvedValue(job({ status: 'cancelled' }));
    conv({ view: view({ run: { active: false, runId: 'r1', startedAt: 'x' } }) });
    const { result } = renderHook(() => useFleetJobStream('job-1', { conversationId: 'conv-1' }));
    await flush();
    expect(result.current.status).toBe('cancelled');
    expect(result.current.isLive).toBe(false);
  });

  it('after the run ends it re-reads the job row until the row settles', async () => {
    jest.useFakeTimers();
    try {
      mockGetJob.mockResolvedValueOnce(job({ status: 'running' })).mockResolvedValueOnce(job({ status: 'running' })).mockResolvedValue(job({ status: 'completed' }));
      conv({ view: view({ run: { active: false, runId: 'r1', startedAt: 'x' } }) });
      const { result } = renderHook(() => useFleetJobStream('job-1', { conversationId: 'conv-1' }));
      await act(async () => { await jest.advanceTimersByTimeAsync(0); });
      await act(async () => { await jest.advanceTimersByTimeAsync(2100); });
      await act(async () => { await jest.advanceTimersByTimeAsync(2100); });
      expect(result.current.status).toBe('completed');
      const calls = mockGetJob.mock.calls.length;
      await act(async () => { await jest.advanceTimersByTimeAsync(6000); });
      expect(mockGetJob.mock.calls.length).toBe(calls); // polling stopped
    } finally {
      jest.useRealTimers();
    }
  });

  it('a big task set stays one tree (RunTree paginates); the adapter result is stable for an unchanged view', async () => {
    mockGetJob.mockResolvedValue(job());
    const tasks: Record<string, TaskView> = { root: { kind: 'workflow_run', parentId: null, status: 'running', phase: 'x', startedAt: '2026-01-01T00:00:00.000Z' } };
    for (let i = 0; i < 300; i++) tasks[`n${i}`] = { kind: 'workflow_node', parentId: 'root', status: 'done', phase: 'finish', startedAt: '2026-01-01T00:00:00.000Z' };
    const v = view({ tasks, run: { active: false, runId: undefined as never, startedAt: undefined as never } });
    conv({ view: v });
    const { result, rerender } = renderHook(() => useFleetJobStream('job-1', { conversationId: 'conv-1' }));
    await flush();
    const tree = result.current.tree;
    expect(tree[0].children).toHaveLength(300);
    rerender();
    expect(result.current.tree).toBe(tree);
  });
});
