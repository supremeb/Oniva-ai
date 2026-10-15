import { activeRunId, isRunActive } from '../run';
import type { ConversationView } from '../types';

const view = (over: Partial<ConversationView> = {}): ConversationView =>
  ({ live: { runId: null, taskId: null, message: null }, ...over }) as ConversationView;

describe('isRunActive', () => {
  it('is false without a view', () => {
    expect(isRunActive(null)).toBe(false);
  });
  it('uses run.active when the engine reports it, even with live cleared', () => {
    expect(isRunActive(view({ run: { active: true, runId: 'r1' } }))).toBe(true);
    expect(activeRunId(view({ run: { active: true, runId: 'r1' } }))).toBe('r1');
    expect(isRunActive(view({ run: { active: false } }))).toBe(false);
    expect(activeRunId(view({ run: { active: false } }))).toBeNull();
  });
  it('run.active=false wins over a stale live run', () => {
    expect(isRunActive(view({ run: { active: false }, live: { runId: 'r1', taskId: 't', message: null } }))).toBe(false);
  });
  it('falls back to live.runId for older engines', () => {
    expect(isRunActive(view({ live: { runId: 'r1', taskId: 't', message: null } }))).toBe(true);
    expect(isRunActive(view())).toBe(false);
  });
});
