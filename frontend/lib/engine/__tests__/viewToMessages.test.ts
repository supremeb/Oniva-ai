/**
 * U6 adapter tests. Oracle: the legacy golden fixtures (KTD6). The engine views
 * in __fixtures__/chat_views.json were produced by the real engine (scripted
 * model and tools, ManualClock) for the same scripted conversations; the
 * adapter must give the persisted `message_parts`, except for the differences
 * asserted explicitly below.
 */
import chatViews from '../__fixtures__/chat_views.json';
import questionViews from '../__fixtures__/question_views.json';
import legacyText from '../../sse/__fixtures__/chat_text.json';
import legacyToolCall from '../../sse/__fixtures__/chat_tool_call.json';
import legacyError from '../../sse/__fixtures__/chat_error.json';
import { applyJsonPatch } from '../patch';
import type { ConversationView, EntryRecord, JsonValue } from '../types';
import { createViewAdapter, viewToMessages, type EngineMessage } from '../viewToMessages';

const views = chatViews as unknown as Record<string, ConversationView>;
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const parts = (m: EngineMessage | undefined) => (m?.richParts ?? []) as any[];

function entry(kind: string, payload: JsonValue, over: Partial<EntryRecord> = {}): EntryRecord {
  return {
    id: over.id ?? `e-${Math.random().toString(36).slice(2, 8)}`,
    conversationId: 'c1',
    taskId: over.taskId ?? null,
    kind,
    payload,
    createdAt: over.createdAt ?? 1000,
  };
}
const user = (id: string, text: string, createdAt = 1000) =>
  entry('user', { submissionId: `s-${id}`, content: text, requestId: `r-${id}` }, { id, createdAt });
const assistant = (id: string, blocks: JsonValue[], createdAt = 1000) =>
  entry('assistant', { runId: 'run', round: 0, message: { role: 'assistant', provider: 'p', model: 'm', blocks }, usage: {} }, { id, createdAt });
const toolResult = (id: string, callId: string, name: string, status: string, content: string, over: Partial<EntryRecord> = {}) =>
  entry(
    'tool_result',
    { runId: 'run', toolCallId: callId, toolName: name, status, content, isError: status !== 'ok', details: null, terminate: false, attempts: 1 },
    { id, ...over },
  );
const call = (id: string, name: string, args: JsonValue = {}) => ({ type: 'tool_call', id, name, arguments: args });

function viewOf(entries: EntryRecord[], over: Partial<ConversationView> = {}): ConversationView {
  const base = clone(views.chat_text);
  return { ...base, entries, live: { runId: null, taskId: null, message: null }, approvals: { items: [] }, toolOutput: {}, ...over };
}

// ---------------------------------------------------------------------------
// Legacy parity (KTD6)
// ---------------------------------------------------------------------------

describe('legacy fixture parity', () => {
  it('chat_text: user message plus the persisted message_parts, exactly', () => {
    const msgs = viewToMessages(views.chat_text);
    expect(msgs.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(msgs[0]).toMatchObject({ content: 'What is the contract?', requestId: 'req-1' });
    expect(parts(msgs[1])).toEqual((legacyText as any).persisted_messages[0].message_parts);
    expect(msgs[1].content).toBe((legacyText as any).persisted_messages[0].response);
    expect(msgs[1].isStreaming).toBe(false);
  });

  it('chat_tool_call: persisted parts, in order, except the asserted differences', () => {
    const msgs = viewToMessages(views.chat_tool_call);
    expect(msgs).toHaveLength(2);
    const got = parts(msgs[1]);
    const legacy: any[] = clone((legacyToolCall as any).persisted_messages[0].message_parts);

    // Difference 1: no `label` (the engine has no display label for a call).
    // Difference 2: no `error: null` on success (the key is simply absent).
    // Difference 3: durationMs is a number measured from entry timestamps
    //   (the fixture holds a placeholder): 1200 ms and 300 ms on the manual clock.
    const durations = got.filter((p) => p.type === 'tool_call').map((p) => p.durationMs);
    expect(durations).toEqual([1200, 300]);
    const normalized = legacy.map((p) => {
      if (p.type !== 'tool_call') return p;
      const { label, error, durationMs, ...rest } = p;
      expect(label).toBeTruthy();
      expect(error).toBeNull();
      expect(durationMs).toBe('<duration>');
      return rest;
    });
    expect(got.map((p) => (p.type === 'tool_call' ? { ...p, durationMs: undefined } : p)).map(stripUndefined)).toEqual(normalized);

    // Both tool_call parts carry toolName (the client-built legacy parts did not).
    expect(got.filter((p) => p.type === 'tool_call').every((p) => typeof p.toolName === 'string' && p.toolName)).toBe(true);
    // Source description comes from the search snippet; no partIndex anywhere.
    expect(got.find((p) => p.type === 'source').description).toBe('a snippet');
    expect(JSON.stringify(got)).not.toContain('partIndex');
    expect(msgs[1].sources).toEqual([{ title: 'Oniva', url: 'https://example.com/oniva', snippet: 'a snippet', sourceType: 'url' }]);
    expect(msgs[1].content).toBe('Done: see the report.');
    expect(msgs[1].reasoning).toBe('Need to search the web. Then write a report.');
  });

  it('chat_error: legacy persisted nothing; the engine keeps the partial text, then an error part with the engine retryable flag', () => {
    expect((legacyError as any).persisted_messages).toEqual([]); // legacy dropped the failed turn
    const sse = (legacyError as any).events.find((e: any) => e.event === 'error').data;
    const msgs = viewToMessages(views.chat_error);
    expect(msgs.map((m) => m.role)).toEqual(['user', 'assistant']);
    // Deliberate: legacy streamed "partial " and then dropped the failed turn; the
    // engine commits the partial reply (payload.partial, stopReason error) before
    // run_error, so the text survives and the error part follows it.
    const got = parts(msgs[1]);
    expect(got.map((p) => p.type)).toEqual(['text', 'error']);
    expect(got[0].content).toBe('partial ');
    expect(got[1]).toEqual({ type: 'error', id: expect.any(String), message: sse.message, title: sse.title, retryable: sse.retryable });
    expect(msgs[1].content).toBe('partial ');
  });
});

const stripUndefined = (v: any): any => JSON.parse(JSON.stringify(v));

// ---------------------------------------------------------------------------
// Entry kinds and tool statuses
// ---------------------------------------------------------------------------

describe('tool result statuses', () => {
  const cases: [string, 'success' | 'error'][] = [
    ['ok', 'success'], ['error', 'error'], ['blocked', 'error'], ['invalid_arguments', 'error'],
    ['unknown_tool', 'error'], ['limit_exceeded', 'error'], ['interrupted', 'error'], ['aborted', 'error'],
    ['denied', 'error'], ['approval_expired', 'error'], ['question_expired', 'error'], ['timeout', 'error'], ['lease_lost', 'error'],
  ];
  it.each(cases)('%s -> %s', (status, expected) => {
    const v = viewOf([
      user('u1', 'go'),
      assistant('a1', [call('c1', 'read', { p: 1 })], 1000),
      toolResult('t1', 'c1', 'read', status, status === 'ok' ? '{"x":1}' : `why ${status}`, { createdAt: 1500 }),
    ]);
    const tool = parts(viewToMessages(v)[1]).find((p) => p.type === 'tool_call');
    expect(tool.status).toBe(expected);
    expect(tool.toolName).toBe('read');
    expect(tool.args).toEqual({ p: 1 });
    expect(tool.durationMs).toBe(500);
    if (expected === 'error') expect(tool.error).toBe(`why ${status}`);
    else expect(tool.result).toEqual({ x: 1 });
  });

  it('a non-JSON ok result stays a string', () => {
    const v = viewOf([user('u1', 'go'), assistant('a1', [call('c1', 'read')]), toolResult('t1', 'c1', 'read', 'ok', 'plain text')]);
    expect(parts(viewToMessages(v)[1])[0].result).toBe('plain text');
  });

  it('a call followed by a later entry without a result is shown as aborted, as the context builder does', () => {
    const v = viewOf([user('u1', 'go'), assistant('a1', [call('c1', 'read')]), user('u2', 'never mind')]);
    const msgs = viewToMessages(v);
    expect(msgs.map((m) => m.role)).toEqual(['user', 'assistant', 'user']);
    expect(parts(msgs[1])[0]).toMatchObject({ status: 'error', error: expect.stringContaining('aborted') });
  });
});

describe('approvals and running tools', () => {
  it('a call with a pending approval is pending and gets an approval card part (credential-scrubbed args)', () => {
    const msgs = viewToMessages(views.approval_pending);
    const tool = parts(msgs[1]).find((p) => p.type === 'tool_call');
    expect(tool).toMatchObject({ id: 'call_report_1', toolName: 'save_report', status: 'pending' });
    const card = parts(msgs[1]).find((p) => p.type === 'approval');
    const item = views.approval_pending.approvals.items[0];
    expect(card).toMatchObject({
      id: item.toolTaskId, toolTaskId: item.toolTaskId, toolName: 'save_report', status: 'pending',
      canDecide: true, description: 'needs a human', expiresAt: null,
    });
    expect(card.items).toEqual([
      { label: 'filename', value: 'report.md' },
      { label: 'report_content', value: '# Report' },
    ]);
    expect(parts(msgs[1]).at(-1)).toBe(card); // after the activity feed
  });

  it('scrubs credential-looking arguments entirely', () => {
    const v = clone(views.approval_pending);
    v.approvals.items[0].arguments = { api_key: 'sk-secret', Authorization: 'Bearer abc', url: 'https://x' };
    const card = parts(viewToMessages(v)[1]).find((p) => p.type === 'approval');
    expect(card.items).toEqual([
      { label: 'api_key', value: '[hidden]', sensitive: true },
      { label: 'Authorization', value: '[hidden]', sensitive: true },
      { label: 'url', value: 'https://x' },
    ]);
    expect(JSON.stringify(card)).not.toContain('sk-secret');
  });

  it('canDecide comes from the adapter option', () => {
    const card = parts(createViewAdapter({ canDecide: false })(views.approval_pending)[1]).find((p) => p.type === 'approval');
    expect(card.canDecide).toBe(false);
  });

  it('decided approvals become resolved parts (denied -> rejected, note kept); cancelled shows nothing', () => {
    const decided = (status: string, decision: unknown) => {
      const v = clone(views.approval_pending);
      Object.assign(v.approvals.items[0], { status, decision, closedAt: 5 });
      return parts(viewToMessages(v)[1]).find((p) => p.type === 'approval');
    };
    expect(decided('approved', { decision: 'approve', principal: 'u1', note: null, decidedAt: 1 })).toMatchObject({ status: 'approved' });
    expect(decided('denied', { decision: 'deny', principal: 'u1', note: 'too risky', decidedAt: 1 })).toMatchObject({ status: 'rejected', note: 'too risky' });
    expect(decided('expired', null)).toMatchObject({ status: 'expired' });
    expect(decided('cancelled', null)).toBeUndefined();
  });

  it('a patch resolving the approval rebuilds the message (card -> resolved)', () => {
    const adapt = createViewAdapter();
    const v1 = clone(views.approval_pending);
    const m1 = adapt(v1);
    const v2 = applyJsonPatch(v1 as unknown as JsonValue, [{ op: 'replace', path: '/approvals/items/0/status', value: 'approved' }]) as unknown as ConversationView;
    const m2 = adapt(v2);
    expect(m2[0]).toBe(m1[0]);
    expect(m2[1]).not.toBe(m1[1]);
    expect(parts(m2[1]).find((p) => p.type === 'approval').status).toBe('approved');
  });

  it('an unresolved call in the last assistant entry is running; its approval-linked output is attached', () => {
    const v = clone(views.approval_pending);
    v.approvals.items[0].status = 'approved';
    v.toolOutput = { [v.approvals.items[0].toolTaskId]: { text: 'writing...', truncated: false } };
    const tool = parts(viewToMessages(v)[1])[0];
    expect(tool.status).toBe('running');
    expect(tool.steps).toEqual([{ id: 'output-call_report_1', label: 'Output', status: 'active', message: 'writing...' }]);
  });

  it('output is linked by toolCallId, exactly, even with several running calls', () => {
    const v = viewOf([user('u1', 'go'), assistant('a1', [call('c1', 'slow'), call('c2', 'slow')])], {
      toolOutput: {
        task_a: { text: 'for c2', truncated: false, attempt: 1, toolCallId: 'c2', toolName: 'slow' },
        task_b: { text: 'for c1', truncated: false, attempt: 1, toolCallId: 'c1', toolName: 'slow' },
      },
    });
    const calls = parts(viewToMessages(v)[1]).filter((p) => p.type === 'tool_call');
    expect(calls.map((c) => [c.id, c.steps?.[0].message])).toEqual([['c1', 'for c1'], ['c2', 'for c2']]);
  });

  it('a linked output never leaks to another call through the heuristic', () => {
    const v = viewOf([user('u1', 'go'), assistant('a1', [call('c1', 'slow')])], {
      toolOutput: { task_a: { text: 'not mine', truncated: false, attempt: 1, toolCallId: 'other', toolName: 'slow' } },
    });
    expect(parts(viewToMessages(v)[1])[0].steps).toBeUndefined();
  });

  it('an unlinked output (toolCallId null) is attached only when exactly one call runs and one output exists', () => {
    const base = [user('u1', 'go'), assistant('a1', [call('c1', 'slow')])];
    const one = viewOf(base, { toolOutput: { task_x: { text: 'tick', truncated: false } } });
    expect(parts(viewToMessages(one)[1])[0].steps?.[0].message).toBe('tick');
    const two = viewOf(base, { toolOutput: { task_x: { text: 'a', truncated: false }, task_y: { text: 'b', truncated: false } } });
    expect(parts(viewToMessages(two)[1])[0].steps).toBeUndefined();
    const parallel = viewOf([user('u1', 'go'), assistant('a1', [call('c1', 'slow'), call('c2', 'slow')])], {
      toolOutput: { task_x: { text: 'a', truncated: false } },
    });
    expect(parts(viewToMessages(parallel)[1]).every((p) => p.steps === undefined)).toBe(true);
  });
});

describe('live message (AE3)', () => {
  it('streams text and reasoning into the assistant message that the commit later keeps', () => {
    const full = views.chat_text;
    const head = full.entries.slice(0, 2); // user, system
    const live = viewOf(head.map((e) => clone(e)), {
      live: { runId: 'r', taskId: 't', message: { role: 'assistant', provider: 'p', model: 'm', blocks: [{ type: 'thinking', thinking: 'hmm' }, { type: 'text', text: 'Hel' }] } },
    });
    const a = viewToMessages(live);
    expect(a.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(a[1].isStreaming).toBe(true);
    expect(parts(a[1])).toEqual([
      { type: 'thinking', variant: 'accordion', thinkingContent: 'hmm', isThinking: false, showElapsed: true },
      { type: 'text', content: 'Hel', isStreaming: true },
    ]);

    const b = viewToMessages(full);
    expect(b[1].id).toBe(a[1].id); // same bubble before and after the commit
    expect(b[1].isStreaming).toBe(false);
    expect(parts(b[1]).filter((p) => p.type === 'text')).toHaveLength(1);
  });

  it('a live thinking block is thinking while it is the last block', () => {
    const v = viewOf([user('u1', 'go')], { live: { runId: 'r', taskId: 't', message: { role: 'assistant', provider: 'p', model: 'm', blocks: [{ type: 'thinking', thinking: 'a' }] } } });
    expect(parts(viewToMessages(v)[1])[0]).toMatchObject({ type: 'thinking', isThinking: true });
  });

  it('live output of a later round extends the committed message; no duplicates after the commit', () => {
    const entries = clone(views.chat_tool_call.entries).slice(0, 4); // user, system, assistant r0, tool_result
    const running = viewOf(entries, {
      live: { runId: 'r', taskId: 't', message: { role: 'assistant', provider: 'p', model: 'm', blocks: [{ type: 'tool_call', id: 'call_report_1', name: 'save_report', arguments: {} }] } },
    });
    const m = viewToMessages(running);
    expect(m).toHaveLength(2);
    expect(parts(m[1]).map((p) => [p.type, p.id ?? '', p.status ?? ''])).toEqual([
      ['thinking', '', ''],
      ['tool_call', 'call_search_1', 'success'],
      ['source', '', ''],
      ['tool_call', 'call_report_1', 'pending'],
    ]);
    const done = viewToMessages(views.chat_tool_call);
    expect(done[1].id).toBe(m[1].id);
    expect(parts(done[1]).filter((p) => p.type === 'tool_call').map((p) => p.id)).toEqual(['call_search_1', 'call_report_1']);
  });

  it('a trailing run_error clears nothing it should not: error then no live', () => {
    const msgs = viewToMessages(views.chat_error);
    expect(msgs[1].isStreaming).toBe(false);
  });
});

describe('grouping and compaction', () => {
  it('one bubble per turn; a steer mid-run is its own user message and the answer continues after it', () => {
    const v = viewOf([
      user('u1', 'start'),
      assistant('a1', [{ type: 'text', text: 'working' }, call('c1', 'read')]),
      toolResult('t1', 'c1', 'read', 'ok', '1'),
      user('u2', 'also this'),
      assistant('a2', [{ type: 'text', text: 'done' }]),
    ]);
    const msgs = viewToMessages(v);
    expect(msgs.map((m) => [m.role, m.content])).toEqual([
      ['user', 'start'], ['assistant', 'working'], ['user', 'also this'], ['assistant', 'done'],
    ]);
    expect(msgs[1].entryIds).toEqual(['a1', 't1']);
  });

  it('consecutive assistant entries of a turn make one message', () => {
    const msgs = viewToMessages(views.chat_tool_call);
    expect(msgs).toHaveLength(2);
    expect(msgs[1].entryIds).toHaveLength(5); // 3 assistant + 2 tool_result entries
  });

  it('a compaction entry is a collapsed marker and hides no earlier entry', () => {
    const v = viewOf([
      user('u1', 'one'), assistant('a1', [{ type: 'text', text: 'first' }]),
      entry('compaction', { summary: 'They said one.', firstKeptEntryId: null }, { id: 'k1' }),
      user('u2', 'two'), assistant('a2', [{ type: 'text', text: 'second' }]),
    ]);
    const msgs = viewToMessages(v);
    expect(msgs.map((m) => m.id)).toEqual(['u1', 'u1:assistant', 'k1', 'u2', 'u2:assistant']);
    expect(parts(msgs[2])).toEqual([
      { type: 'info', title: 'Earlier messages summarized', message: 'Earlier messages summarized', details: 'They said one.' },
    ]);
    expect(msgs[1].content).toBe('first');
  });

  it('ignores system and unknown entry kinds; user content may be a text array', () => {
    const v = viewOf([
      entry('system', { text: 'p' }), entry('mystery', { x: 1 }),
      entry('user', { submissionId: 's', content: [{ type: 'text', text: 'a' }, { type: 'text', text: 'b' }] }, { id: 'u1' }),
    ]);
    const msgs = viewToMessages(v);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].content).toBe('ab');
    expect(msgs[0].requestId).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Derived parts
// ---------------------------------------------------------------------------

describe('source and file parts from tool results', () => {
  const run = (name: string, content: unknown) =>
    parts(viewToMessages(viewOf([user('u1', 'x'), assistant('a1', [call('c1', name, { q: 1 })]), toolResult('t1', 'c1', name, 'ok', JSON.stringify(content))]))[1]);

  it('search_knowledge: one document source per document, KB-scoped url', () => {
    const p = run('search_knowledge', {
      success: true,
      results: [
        { content: 'aaa', source: 'Policy.pdf', document_id: 'd1', knowledge_base_id: 'kb1' },
        { content: 'bbb', source: 'Policy.pdf', document_id: 'd1', knowledge_base_id: 'kb1' },
        { content: 'ccc', source: 'Other.pdf', document_id: 'd2', knowledge_base_id: 'kb1' },
      ],
    }).filter((x) => x.type === 'source');
    expect(p).toEqual([
      { type: 'source', sourceType: 'document', title: 'Policy.pdf', url: '/knowledge-bases/kb1/documents/d1', description: 'aaa' },
      { type: 'source', sourceType: 'document', title: 'Other.pdf', url: '/knowledge-bases/kb1/documents/d2', description: 'ccc' },
    ]);
  });

  it('memory recall: at most five memory sources', () => {
    const results = Array.from({ length: 7 }, (_, i) => ({ id: `m${i}`, action_type: 'approve', context_decision: 'because' }));
    const p = run('search_past_decisions', { results }).filter((x) => x.type === 'source');
    expect(p).toHaveLength(5);
    expect(p[0]).toMatchObject({ sourceType: 'memory', title: 'Memory: approve', url: '/memory?id=m0', description: 'because' });
  });

  it('write_file-class results become a file part without content; output_files are not duplicated', () => {
    const w = run('write_file', { success: true, path: '/ws/notes/a.md', size: 3, content: 'secret body' }).filter((x) => x.type === 'file');
    expect(w).toEqual([{ type: 'file', filename: 'a.md', size: 3, fileType: 'text/markdown', toolCallId: 'c1' }]);
    const r = run('run_code', { success: true, output_files: ['/out/chart.png'] }).filter((x) => x.type === 'file');
    expect(r).toEqual([{ type: 'file', filename: 'chart.png', fileType: 'image/png', toolCallId: 'c1' }]);
  });

  it('no file or source parts for failed calls or unrelated results', () => {
    const failed = parts(viewToMessages(viewOf([user('u1', 'x'), assistant('a1', [call('c1', 'web_search')]), toolResult('t1', 'c1', 'web_search', 'error', '{"results":[{"url":"https://x"}]}')]))[1]);
    expect(failed.map((p) => p.type)).toEqual(['tool_call']);
    expect(run('calculator', { success: true, value: 3 }).map((p) => p.type)).toEqual(['tool_call']);
  });
});

// ---------------------------------------------------------------------------
// Memoization
// ---------------------------------------------------------------------------

describe('memoization', () => {
  const patch = (view: ConversationView, ops: any[]) => applyJsonPatch(view as unknown as JsonValue, ops) as unknown as ConversationView;

  it('appending an entry leaves earlier messages referentially equal', () => {
    const adapt = createViewAdapter();
    const v1 = viewOf([user('u1', 'one'), assistant('a1', [{ type: 'text', text: 'first' }])]);
    const m1 = adapt(v1);
    const v2 = patch(v1, [
      { op: 'add', path: '/entries/2', value: user('u2', 'two') },
    ]);
    const m2 = adapt(v2);
    expect(m2).toHaveLength(3);
    expect(m2[0]).toBe(m1[0]);
    expect(m2[1]).toBe(m1[1]);
  });

  it('live and toolOutput patches rebuild only the trailing message', () => {
    const adapt = createViewAdapter();
    let v = viewOf([
      user('u1', 'one'), assistant('a1', [{ type: 'text', text: 'first' }]),
      user('u2', 'two'), assistant('a2', [call('c1', 'slow')]),
    ]);
    const m1 = adapt(v);
    v = patch(v, [{ op: 'add', path: '/toolOutput/task_1', value: { text: 'x', truncated: false } }]);
    const m2 = adapt(v);
    expect([0, 1, 2].map((i) => m2[i] === m1[i])).toEqual([true, true, true]);
    expect(m2[3]).not.toBe(m1[3]);
    v = patch(v, [{ op: 'replace', path: '/toolOutput/task_1/text', value: 'xy' }]);
    const m3 = adapt(v);
    expect([0, 1, 2].map((i) => m3[i] === m2[i])).toEqual([true, true, true]);
    expect(parts(m3[3])[0].steps[0].message).toBe('xy');
  });

  it('a late tool result rebuilds the message that owns the call', () => {
    const adapt = createViewAdapter();
    const v1 = viewOf([user('u1', 'x'), assistant('a1', [call('c1', 'read')])]);
    const m1 = adapt(v1);
    const v2 = patch(v1, [{ op: 'add', path: '/entries/2', value: toolResult('t1', 'c1', 'read', 'ok', '1') }]);
    const m2 = adapt(v2);
    expect(m2[0]).toBe(m1[0]);
    expect(parts(m2[1])[0].status).toBe('success');
  });
});

describe('partial assistant entries (failed runs)', () => {
  it('renders the partial text then the error; the never-run tool calls of a partial reply are skipped', () => {
    const partial = entry(
      'assistant',
      { runId: 'run', round: 0, partial: true, message: { role: 'assistant', provider: 'p', model: 'm', blocks: [{ type: 'text', text: 'Looking' }, call('c9', 'read')], stopReason: 'error' }, usage: {} },
      { id: 'a1' },
    );
    const err = entry('run_error', { runId: 'run', error: { kind: 'x', message: 'boom', retryable: false } }, { id: 'r1' });
    const msgs = viewToMessages(viewOf([user('u1', 'go'), partial, err]));
    const got = parts(msgs[1]);
    expect(got.map((p) => p.type)).toEqual(['text', 'error']);
    expect(msgs[1].runId).toBe('run');
    expect(msgs[1].isStreaming).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// ask_user questions (approvals plan U12). Oracle: question_views.json, produced
// by the real engine (recorder: engine/test/watch/question-fixtures.test.ts).
// ---------------------------------------------------------------------------

describe('questions', () => {
  const qviews = questionViews as unknown as Record<string, ConversationView>;
  const tail = (v: ConversationView) => parts(viewToMessages(v).find((m) => m.role === 'assistant'));

  it('a pending question is a pending call plus a question card keyed by the tool task', () => {
    const v = qviews.question_pending;
    const item = v.questions.items[0];
    const ps = tail(v);
    expect(ps.find((p) => p.type === 'tool_call')).toMatchObject({ toolName: 'ask_user', status: 'pending' });
    const q = ps.find((p) => p.type === 'question');
    expect(q).toMatchObject({
      id: item.toolTaskId, toolTaskId: item.toolTaskId, question: item.question, status: 'pending',
      multiSelect: true, allowFreeText: true, canDecide: true, expiresAt: item.expiresAt,
    });
    expect(q.options).toEqual([{ id: 'staging', label: 'Staging' }, { id: 'prod', label: 'Production', description: 'customer facing' }]);
  });

  it('canDecide follows the adapter option', () => {
    const q = parts(createViewAdapter({ canDecide: false })(qviews.question_pending).find((m) => m.role === 'assistant')).find((p) => p.type === 'question');
    expect(q.canDecide).toBe(false);
  });

  it('an answered question keeps the card read-only with the answer; the call succeeded', () => {
    const ps = tail(qviews.question_answered);
    expect(ps.find((p) => p.type === 'tool_call')).toMatchObject({ status: 'success' });
    expect(ps.find((p) => p.type === 'question')).toMatchObject({
      status: 'answered', answer: { optionIds: ['staging'], text: 'and the canary' },
    });
  });

  it('an expired question maps the question_expired result to an error line and an expired card', () => {
    const ps = tail(qviews.question_expired);
    expect(ps.find((p) => p.type === 'tool_call')).toMatchObject({ status: 'error', error: expect.stringContaining('expired') });
    expect(ps.find((p) => p.type === 'question')).toMatchObject({ status: 'expired' });
  });

  it('a cancelled question renders no card', () => {
    const v = clone(qviews.question_pending);
    v.questions.items[0].status = 'cancelled';
    expect(tail(v).some((p) => p.type === 'question')).toBe(false);
  });

  it('a view without `questions` (older engine) still adapts', () => {
    const v = clone(views.chat_tool_call) as any;
    delete v.questions;
    expect(() => viewToMessages(v)).not.toThrow();
  });

  it('a patch answering the question yields a new message with the answered card', () => {
    const adapt = createViewAdapter();
    const v1 = qviews.question_pending;
    const m1 = adapt(v1);
    const v2 = applyJsonPatch(v1 as unknown as JsonValue, [
      { op: 'replace', path: '/questions/items/0/status', value: 'answered' },
      { op: 'replace', path: '/questions/items/0/answer', value: { optionIds: ['prod'], text: null, principal: 'u', answeredAt: 1 } },
    ]) as unknown as ConversationView;
    const m2 = adapt(v2);
    expect(m2[0]).toBe(m1[0]);
    expect(parts(m2[m2.length - 1]).find((p) => p.type === 'question').status).toBe('answered');
  });
});
