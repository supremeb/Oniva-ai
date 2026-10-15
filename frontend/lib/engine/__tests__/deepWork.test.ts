import { applyJsonPatch } from '../patch';
import { createDeepWorkAdapter, parseWorkerId, viewToDeepWork } from '../deepWork';
import type { ConversationView, EntryRecord, JsonValue } from '../types';

// ---- view builders (shapes recorded from engine/src/watch/view.ts + context-builder) ----
const ZERO = { requests: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: null };

let seq = 0;
const entry = (kind: string, payload: JsonValue, taskId: string | null = null): EntryRecord => ({
  id: `ent_${++seq}`,
  conversationId: 'conv_root',
  taskId,
  kind,
  payload,
  createdAt: 1_000_000 + seq * 1000,
});

const call = (id: string, name: string, args: JsonValue) => ({ type: 'tool_call', id, name, arguments: args });
const assistant = (...blocks: JsonValue[]) =>
  entry('assistant', { runId: 'run1', round: 0, message: { role: 'assistant', blocks, stopReason: 'tool_use' } });
const result = (callId: string, name: string, taskId: string, content: unknown, status = 'ok', details: JsonValue = null) =>
  entry(
    'tool_result',
    {
      runId: 'run1',
      toolCallId: callId,
      toolName: name,
      status,
      content: typeof content === 'string' ? content : JSON.stringify(content),
      isError: status !== 'ok',
      details,
      terminate: false,
      attempts: 1,
    },
    taskId,
  );

const sub = (conv: string, over: Partial<{ status: string; recent: string[]; toolCalls: number; endedAt: string }> = {}) => ({
  conversationId: conv,
  submissionId: `subm_${conv}`,
  status: 'running',
  startedAt: '2026-10-15T10:00:00.000Z',
  toolCalls: 0,
  recent: [],
  usage: { ...ZERO, inputTokens: 100, outputTokens: 50 },
  ...over,
});

function view(entries: EntryRecord[], extra: Partial<ConversationView> = {}): ConversationView {
  return {
    entries,
    live: { runId: null, taskId: null, message: null },
    inbox: { queue: [], placed: [] },
    approvals: { items: [] },
    usage: { requests: 3, total: { ...ZERO, inputTokens: 1000, outputTokens: 200 }, byModel: {}, byPurpose: {} },
    agent: {},
    toolOutput: {},
    run: { active: true, runId: 'run1', startedAt: '2026-10-15T10:00:00.000Z' },
    subagents: {},
    ...extra,
  } as ConversationView;
}

const round1 = call('c_round1', 'dw_start_round', {
  round: 1,
  workers: [
    { worker_id: 'w1', sub_query: 'Market size of X' },
    { worker_id: 'w2', sub_query: 'Competitors of X' },
  ],
});
const startRound1 = (): EntryRecord[] => [
  entry('user', { content: 'Research X' }),
  assistant(round1),
  result('c_round1', 'dw_start_round', 't_round1', { round: 1, registered: 2 }),
];
const workerCalls = () =>
  assistant(
    call('c_w1', 'research_worker', { prompt: 'worker_id: w1\nsub_query: Market size of X' }),
    call('c_w2', 'research_worker', { prompt: 'worker_id: w2\nsub_query: Competitors of X' }),
  );
const w1Answer = {
  summary: 'Market is large',
  learning_count: 12,
  source_count: 2,
  top_findings: ['$5B in 2025', 'Growing 12% a year'],
  sources: [
    { title: 'Report', url: 'https://www.example.com/report', type: 'web' },
    { title: 'Internal KB', url: '', type: 'knowledge_base' },
  ],
};

function midResearch(recent: string[] = ['web_search: competitors of X']): ConversationView {
  return view(
    [...startRound1(), workerCalls(), result('c_w1', 'research_worker', 't_w1', w1Answer)],
    {
      toolOutput: { t_w2: { text: '', truncated: false, toolCallId: 'c_w2', toolName: 'research_worker' } },
      subagents: {
        t_w1: sub('child_w1', { status: 'done', toolCalls: 7, endedAt: '2026-10-15T10:01:30.000Z' }),
        t_w2: sub('child_w2', { recent, toolCalls: recent.length }),
      } as never,
    },
  );
}

describe('parseWorkerId', () => {
  it.each([
    ['worker_id: w1\nsub_query: x', 'w1'],
    ['Worker ID = "g2"', 'g2'],
    ['w3: find competitors', 'w3'],
    ['Find competitors of X', null],
  ])('%j -> %j', (prompt, id) => expect(parseWorkerId(prompt)).toBe(id));
});

describe('viewToDeepWork', () => {
  it('AE4: mid-research renders the plan, one finished worker and one running worker with recent activity', () => {
    const s = viewToDeepWork(midResearch(), { planTitle: 'X report' });
    expect(s.status).toBe('executing');
    expect(s.phase).toBe('researching');
    expect(s.plan).toEqual({ title: 'X report', steps: ['Market size of X', 'Competitors of X'] });
    expect(s.rounds).toEqual([{ round: 1, kind: 'plan', workerIds: ['w1', 'w2'], status: 'running' }]);

    const [w1, w2] = s.workers;
    expect(w1).toMatchObject({
      key: 'c_w1',
      worker_id: 'w1',
      sub_query: 'Market size of X',
      status: 'complete',
      learning_count: 12,
      taskId: 't_w1',
      childConversationId: 'child_w1',
      round: 1,
    });
    expect(w1?.completionSummary).toEqual({
      findings: ['$5B in 2025', 'Growing 12% a year'],
      sourceCount: 2,
      duration: 90, // from the child's task timestamps (KTD9)
      toolCallCount: 7,
    });
    expect(w2).toMatchObject({
      worker_id: 'w2',
      status: 'researching',
      taskId: 't_w2',
      childConversationId: 'child_w2',
      recentActions: ['web_search: competitors of X'],
      activity: 'web_search: competitors of X',
    });
    expect(s.sources.map((x) => x.url ?? x.title)).toEqual(['https://www.example.com/report', 'Internal KB']);
    expect(s.sources[0]).toMatchObject({ type: 'url', domain: 'example.com' });
    expect(s.sources[1]?.type).toBe('knowledge_base');
    expect(s.usage).toMatchObject({ totalTokens: 1200, workerTokens: 300, requests: 3 });
  });

  it('AE4: a patch to one worker leaves the others referentially equal', () => {
    const adapt = createDeepWorkAdapter();
    const v1 = midResearch();
    const a = adapt(v1);
    const patched = applyJsonPatch(v1 as unknown as JsonValue, [
      { op: 'replace', path: '/subagents/t_w2/recent', value: ['web_search: competitors of X', 'browse_url: example.com'] },
    ]) as unknown as ConversationView;
    const b = adapt(patched);
    expect(b).not.toBe(a);
    expect(b.workers[0]).toBe(a.workers[0]);
    expect(b.workers[1]).not.toBe(a.workers[1]);
    expect(b.workers[1]?.recentActions).toHaveLength(2);
    expect(b.rounds).toBe(a.rounds);
    expect(b.sources).toBe(a.sources);
    expect(b.plan).toBe(a.plan);
    // an unrelated patch returns the very same state object
    expect(adapt(patched)).toBe(b);
  });

  it('links an unlinked running worker to the spare subagent in call order, and falls back to w<n> ids', () => {
    const v = view(
      [
        entry('user', { content: 'q' }),
        assistant(call('c_a', 'research_worker', { prompt: 'Look into topic A' }), call('c_b', 'research_worker', { prompt: 'Look into topic B' })),
      ],
      {
        subagents: {
          t_2: sub('child_2'),
          t_1: { ...sub('child_1'), startedAt: '2026-10-15T09:59:00.000Z' },
        } as never,
      },
    );
    const s = viewToDeepWork(v);
    expect(s.workers.map((w) => [w.worker_id, w.taskId, w.childConversationId])).toEqual([
      ['w1', 't_1', 'child_1'],
      ['w2', 't_2', 'child_2'],
    ]);
    expect(s.workers[0]?.sub_query).toBe('Look into topic A');
  });

  it('a worker answer that is not JSON yields zero findings and the raw summary, not a crash', () => {
    const v = view([
      ...startRound1(),
      workerCalls(),
      result('c_w1', 'research_worker', 't_w1', 'I looked around and found nothing structured.'),
      result('c_w2', 'research_worker', 't_w2', '```json\n{"summary":"fenced","learning_count":3,"top_findings":["f"]}\n```'),
    ]);
    const [w1, w2] = viewToDeepWork(v).workers;
    expect(w1).toMatchObject({ status: 'complete', learning_count: 0, summary: 'I looked around and found nothing structured.' });
    expect(w1?.completionSummary?.findings).toEqual([]);
    expect(w2).toMatchObject({ learning_count: 3, summary: 'fenced' });
  });

  it('a failed worker result is an error row with the message', () => {
    const v = view([...startRound1(), workerCalls(), result('c_w1', 'research_worker', 't_w1', 'child failed: quota', 'error')]);
    expect(viewToDeepWork(v).workers[0]).toMatchObject({ status: 'error', error: 'child failed: quota' });
  });

  it('reports reflections and gap-task rounds', () => {
    const gap = call('c_round2', 'dw_start_round', { round: 2, workers: [{ worker_id: 'g1', sub_query: 'Pricing of X' }] });
    const v = view([
      ...startRound1(),
      workerCalls(),
      result('c_w1', 'research_worker', 't_w1', w1Answer),
      result('c_w2', 'research_worker', 't_w2', w1Answer),
      assistant(call('c_r1', 'reflector', { prompt: 'round 1' })),
      result('c_r1', 'reflector', 't_r1', '{"score":62,"gaps":["No pricing data"],"round":1,"continuing":true}'),
      assistant(gap),
      result('c_round2', 'dw_start_round', 't_round2', { round: 2 }),
      assistant(call('c_g1', 'research_worker', { prompt: 'worker_id: g1\nPricing of X' })),
    ]);
    const s = viewToDeepWork(v);
    expect(s.reflections).toEqual([{ round: 1, score: 62, gaps: ['No pricing data'], continuing: true }]);
    expect(s.rounds.map((r) => [r.round, r.kind, r.status])).toEqual([
      [1, 'plan', 'done'],
      [2, 'gap', 'running'],
    ]);
    expect(s.workers[2]).toMatchObject({ worker_id: 'g1', round: 2, sub_query: 'Pricing of X' });
    expect(s.plan?.steps).toEqual(['Market size of X', 'Competitors of X']);
  });

  it('a refused dw_start_round registers nothing', () => {
    const v = view([
      entry('user', { content: 'q' }),
      assistant(round1),
      result('c_round1', 'dw_start_round', 't1', 'Too many workers', 'error'),
    ]);
    const s = viewToDeepWork(v);
    expect(s.rounds).toEqual([]);
    expect(s.plan).toBeNull();
  });

  it('report follows section call order and a replaced heading updates in place', () => {
    const sec = (id: string, heading: string, markdown: string) => [
      assistant(call(id, 'dw_write_section', { heading, markdown })),
      result(id, 'dw_write_section', `t_${id}`, { heading }),
    ];
    const v = view([
      ...startRound1(),
      ...sec('s1', 'Introduction', 'Intro v1'),
      ...sec('s2', 'Findings', 'Findings text'),
      ...sec('s3', 'Introduction', 'Intro v2'),
      assistant(call('s4', 'dw_write_section', { heading: 'Rejected', markdown: 'x' })),
      result('s4', 'dw_write_section', 't_s4', 'Round still open', 'error'),
    ]);
    const s = viewToDeepWork(v);
    expect(s.sections.map((x) => x.heading)).toEqual(['Introduction', 'Findings']);
    expect(s.report).toBe('## Introduction\n\nIntro v2\n\n## Findings\n\nFindings text');
    expect(s.phase).toBe('synthesizing');
  });

  it('derives the phase from the running tool call', () => {
    const base = startRound1();
    expect(viewToDeepWork(view([...base, assistant(call('x', 'reflector', { prompt: 'r' }))])).phase).toBe('reflecting');
    expect(viewToDeepWork(view([...base, assistant(call('x', 'output_agent', { prompt: 'deck' }))])).phase).toBe('generating_output');
    expect(viewToDeepWork(view([...base, assistant(call('x', 'dw_write_section', { heading: 'A', markdown: 'b' }))])).phase).toBe('synthesizing');
    expect(viewToDeepWork(view(base)).phase).toBe('researching');
  });

  it('a successful dw_finalize completes the run with artifacts and merged sources', () => {
    const v = view(
      [
        ...startRound1(),
        workerCalls(),
        result('c_w1', 'research_worker', 't_w1', w1Answer),
        assistant(call('c_pub', 'publish_artifact', { path: '/workspace/deck.pptx' })),
        result('c_pub', 'publish_artifact', 't_pub', {
          status: 'published',
          artifact_uri: 'artifact://a1',
          download_url: '/dl/a1',
          filename: 'deck.pptx',
          size_bytes: 2048,
        }),
        assistant(call('c_fin', 'dw_finalize', {})),
        result('c_fin', 'dw_finalize', 't_fin', {
          report_length: 4000,
          artifacts: [
            { artifact_uri: 'artifact://a1', download_url: '/dl/a1', filename: 'deck.pptx', size: 2048 },
            { filename: 'report.md', size: 4000 },
          ],
          sources: [{ title: 'Extra', url: 'https://extra.example.org/p', type: 'web' }],
        }),
      ],
      { run: { active: false } },
    );
    const s = viewToDeepWork(v);
    expect(s.status).toBe('completed');
    expect(s.phase).toBeNull();
    expect(s.artifacts).toEqual([
      { type: 'file', filename: 'deck.pptx', size: 2048, downloadUrl: '/dl/a1', artifactUri: 'artifact://a1' },
      { type: 'file', filename: 'report.md', size: 4000 },
    ]);
    expect(s.sources.map((x) => x.url ?? x.title)).toEqual(['https://www.example.com/report', 'Internal KB', 'https://extra.example.org/p']);
    expect(s.endedAt).not.toBeNull();
  });

  it('failure: a run_error with no finalize is the error state; abort is cancelled', () => {
    const failed = view([...startRound1(), entry('run_error', { error: { message: 'model unavailable', code: 'provider_error' } })], {
      run: { active: false },
    });
    expect(viewToDeepWork(failed)).toMatchObject({ status: 'error', error: 'model unavailable', phase: null });

    const cancelled = view([...startRound1(), entry('run_error', { error: { message: 'Run aborted', code: 'aborted' } })], {
      run: { active: false },
    });
    expect(viewToDeepWork(cancelled).status).toBe('cancelled');

    // backend status wins when the view is ambiguous
    const idle = view(startRound1(), { run: { active: false } });
    expect(viewToDeepWork(idle).status).toBe('executing');
    expect(viewToDeepWork(idle, { status: 'cancelled' }).status).toBe('cancelled');
    expect(viewToDeepWork(idle, { status: 'failed', error: 'engine lost' })).toMatchObject({ status: 'error', error: 'engine lost' });
  });

  it('AE10: an interrupted result sets recovering; the next orchestrator turn clears it but keeps the marker', () => {
    const base = [...startRound1(), workerCalls()];
    const interrupted = view([...base, result('c_w1', 'research_worker', 't_w1', 'The tool was interrupted by a restart', 'interrupted')]);
    const during = viewToDeepWork(interrupted);
    expect(during).toMatchObject({ resumed: true, recovering: true });
    expect(during.workers[0]).toMatchObject({ status: 'error' });

    const later = view([...(interrupted.entries), assistant(call('c_w1b', 'research_worker', { prompt: 'worker_id: w1\nretry' }))]);
    expect(viewToDeepWork(later)).toMatchObject({ resumed: true, recovering: false });

    expect(viewToDeepWork(view(base))).toMatchObject({ resumed: false, recovering: false });
    const marker = view([...base, entry('run_resumed', {})]);
    expect(viewToDeepWork(marker)).toMatchObject({ resumed: true, recovering: true });
  });

  it('is total on garbage: null view, odd payloads', () => {
    expect(viewToDeepWork(null).workers).toEqual([]);
    const odd = view([
      entry('assistant', 'nope' as never),
      entry('assistant', { message: { blocks: [{ type: 'tool_call', id: 5 }] } }),
      entry('tool_result', { toolCallId: 'x', content: [{ type: 'text', text: '{}' }] }),
    ]);
    expect(() => viewToDeepWork(odd)).not.toThrow();
    const older = { ...midResearch(), subagents: undefined } as ConversationView;
    expect(viewToDeepWork(older).workers[1]?.status).toBe('researching');
  });
});
