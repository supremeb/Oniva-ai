/**
 * Golden Deep Work views (plan 2026-10-15-002, U11). `__fixtures__/deep_work_views.json` is a copy of
 * engine/test/fixtures/deep-work/deep_work_views.json, recorded from the real engine's scripted run
 * (orchestrator config from the backend's compose_execute_config, one worker per round, a reflection that
 * adds gap-1, a second that stops, two sections, finalize). viewToDeepWork must give the legacy panel state
 * for each recorded point; applying the recorded frames must reproduce the last point's view.
 */
import views from '../__fixtures__/deep_work_views.json';
import { viewToDeepWork } from '../deepWork';
import { applyJsonPatch } from '../patch';
import type { ConversationView, JsonValue } from '../types';

type Point = { at: string; view: ConversationView };
type Scenario = { legacy: string | null; points: Point[]; frames: any[] };
const fixtures = views as unknown as Record<string, Scenario>;
const at = (name: string, point: string) => {
  const p = fixtures[name].points.find((x) => x.at === point);
  if (!p) throw new Error(`no point ${point}`);
  return p.view;
};

describe('deep work golden views', () => {
  it('success: running worker, then reflecting, then completed', () => {
    const running = viewToDeepWork(at('success', 'worker-running'));
    expect(running.status).toBe('executing');
    expect(running.phase).toBe('researching');
    expect(running.rounds).toMatchObject([{ round: 1, kind: 'plan', workerIds: ['w1'], status: 'running' }]);
    expect(running.workers).toHaveLength(1);
    expect(running.workers[0]).toMatchObject({ worker_id: 'w1', status: 'researching', round: 1 });
    expect(running.workers[0].recentActions).toEqual(['web_search({"query":"q-w1"})']);
    expect(running.workers[0].childConversationId).toBeTruthy();

    const reflecting = viewToDeepWork(at('success', 'reflecting'));
    expect(reflecting.phase).toBe('reflecting');
    expect(reflecting.workers[0]).toMatchObject({ status: 'complete', learning_count: 2 });
    expect(reflecting.workers[0].sources).toHaveLength(2);
    expect(reflecting.rounds[0].status).toBe('done');

    const done = viewToDeepWork(at('success', 'done'));
    expect(done.status).toBe('completed');
    expect(done.phase).toBeNull();
    expect(done.plan?.steps).toEqual(['q-w1']);
    expect(done.rounds.map((r) => [r.round, r.kind, r.workerIds])).toEqual([
      [1, 'plan', ['w1']],
      [2, 'gap', ['gap-1']],
    ]);
    expect(done.workers.map((w) => [w.worker_id, w.status, w.round])).toEqual([
      ['w1', 'complete', 1],
      ['gap-1', 'complete', 2],
    ]);
    expect(done.reflections.map((r) => [r.round, r.score, r.continuing])).toEqual([
      [1, 55, true],
      [2, 88, false],
    ]);
    expect(done.reflections[0].gaps).toEqual(['gap-1: the second angle']);
    expect(done.sections.map((s) => s.heading)).toEqual(['Findings', 'Gaps closed']);
    expect(done.report).toContain('## Gaps closed');
    expect(done.sources.map((s) => s.url)).toEqual(
      expect.arrayContaining(['https://example.com/w1/1', 'https://example.com/gap-1/2']),
    );
    expect(done.artifacts).toEqual([]);
    expect(done.error).toBeNull();
    expect(done.usage.requests).toBe(10);
    expect(done.usage.workerTokens).toBeGreaterThan(0);
  });

  it('failure: the worker error stays in its row, the run ends in error with the orchestrator message', () => {
    const faulted = viewToDeepWork(at('failure', 'worker-faulted'));
    expect(faulted.status).toBe('executing');
    expect(faulted.phase).toBe('reflecting');
    expect(faulted.workers).toHaveLength(1);
    expect(faulted.workers[0]).toMatchObject({ worker_id: 'w1', status: 'error' });
    expect(faulted.workers[0].error).toContain('worker failed');

    const failed = viewToDeepWork(at('failure', 'run-failed'));
    expect(failed.status).toBe('error');
    expect(failed.phase).toBeNull();
    expect(failed.error).toBe('orchestrator failed');
    expect(failed.workers[0]).toMatchObject({ status: 'error' });
    expect(failed.workers[0].error).toContain('worker failed');
    expect(failed.reflections).toHaveLength(1);
    expect(failed.sections).toEqual([]);
  });

  it('cancel: no running worker; the backend status decides cancelled (the engine writes no run_error for a tool-phase abort)', () => {
    const running = viewToDeepWork(at('cancel', 'worker-running'));
    expect(running.workers.map((w) => w.status)).toEqual(['researching']);

    const view = at('cancel', 'cancelled');
    expect(view.run?.active).toBe(false);
    const cancelled = viewToDeepWork(view, { status: 'cancelled' });
    expect(cancelled.status).toBe('cancelled');
    expect(cancelled.phase).toBeNull();
    expect(cancelled.workers).toHaveLength(1);
    expect(cancelled.workers.some((w) => w.status === 'researching')).toBe(false);
    expect(cancelled.workers[0].status).toBe('error');
    expect(cancelled.endedAt).not.toBeNull();
    expect(cancelled.sections).toEqual([]);
  });

  it.each(['success', 'failure', 'cancel'])('%s: the recorded frames rebuild the last view', (name) => {
    const { frames, points } = fixtures[name];
    expect('snapshot' in frames[0]).toBe(true);
    let view: JsonValue = frames[0].snapshot;
    for (const f of frames.slice(1)) {
      if ('snapshot' in f) view = f.snapshot;
      else view = applyJsonPatch(view, f.patch);
    }
    expect(view).toEqual(points[points.length - 1].view);
    expect(viewToDeepWork(view as unknown as ConversationView)).toEqual(
      viewToDeepWork(points[points.length - 1].view),
    );
  });
});
