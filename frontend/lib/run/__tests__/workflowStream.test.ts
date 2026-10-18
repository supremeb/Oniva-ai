import { applyStreamEvent, emptyLiveRun, liveRunToTree } from '../workflowStream';

const run = (events: Array<Record<string, unknown>>) => events.reduce((r, e) => applyStreamEvent(r, e as { type: string }), emptyLiveRun());

describe('applyStreamEvent', () => {
  it('progress marks a node running and active; node_complete settles it', () => {
    let r = run([{ type: 'progress', node_id: 'a', status: 'running' }]);
    expect(r.nodes.a.status).toBe('running');
    expect(r.activeNodeId).toBe('a');
    r = applyStreamEvent(r, { type: 'node_complete', node_id: 'a', result: { status: 'success', output: { text: 'hi' }, error: null } });
    expect(r.nodes.a).toMatchObject({ status: 'completed', output: { text: 'hi' } });
    expect(r.activeNodeId).toBeNull();
  });

  it('node_skipped yields a skipped node (a skipped branch)', () => {
    const r = run([
      { type: 'progress', node_id: 'if', status: 'running' },
      { type: 'node_complete', node_id: 'if', result: { status: 'success' } },
      { type: 'node_skipped', node_id: 'no', status: 'skipped' },
    ]);
    expect(r.order).toEqual(['if', 'no']);
    expect(r.nodes.no.status).toBe('skipped');
  });

  it('a failed node_complete followed by error fails the node and the run', () => {
    const r = run([
      { type: 'progress', node_id: 'a', status: 'running' },
      { type: 'node_complete', node_id: 'a', result: { status: 'failed', error: 'boom' } },
      { type: 'error', node_id: 'a', error: 'boom' },
    ]);
    expect(r.nodes.a).toMatchObject({ status: 'failed', error: 'boom' });
    expect(r.ended).toBe('failed');
    expect(r.error).toBe('boom');
  });

  it('a run-level error without a node does not invent a node', () => {
    const r = run([{ type: 'error', error: 'engine down' }]);
    expect(r.order).toEqual([]);
    expect(r.ended).toBe('failed');
  });

  it('complete records the final status and results for nodes not seen', () => {
    const r = run([{ type: 'complete', status: 'completed', results: { x: { status: 'skipped' }, y: { status: 'success' } } }]);
    expect(r.ended).toBe('completed');
    expect(r.nodes.x.status).toBe('skipped');
    expect(r.nodes.y.status).toBe('completed');
  });

  it('complete cancelled is kept; an unknown complete status falls back to completed', () => {
    expect(run([{ type: 'complete', status: 'cancelled', results: {} }]).ended).toBe('cancelled');
    expect(run([{ type: 'complete', results: {} }]).ended).toBe('completed');
  });

  it('accepts the legacy node_started event and a waiting progress status', () => {
    expect(run([{ type: 'node_started', node_id: 'a' }]).nodes.a.status).toBe('running');
    expect(run([{ type: 'progress', node_id: 'h', status: 'waiting' }]).nodes.h.status).toBe('waiting');
  });

  it('ignores unknown events and returns the same object', () => {
    const r = emptyLiveRun();
    expect(applyStreamEvent(r, { type: 'heartbeat' })).toBe(r);
  });
});

describe('liveRunToTree', () => {
  it('builds a run root over the nodes in arrival order with names', () => {
    const r = run([
      { type: 'progress', node_id: 'a', status: 'running' },
      { type: 'node_complete', node_id: 'a', result: { status: 'success' } },
      { type: 'node_skipped', node_id: 'b' },
      { type: 'progress', node_id: 'c', status: 'running' },
    ]);
    const [root] = liveRunToTree(r, { a: 'Fetch' });
    expect(root).toMatchObject({ kind: 'run', label: 'Workflow run', status: 'running' });
    expect(root.children.map((c) => [c.label, c.status])).toEqual([['Fetch', 'completed'], ['b', 'skipped'], ['c', 'running']]);
  });
  it('the root takes the final status once complete', () => {
    const r = run([{ type: 'complete', status: 'failed', results: {} }]);
    expect(liveRunToTree(r)[0].status).toBe('failed');
  });
  it('is empty before anything arrives', () => {
    expect(liveRunToTree(emptyLiveRun())).toEqual([]);
  });
});
