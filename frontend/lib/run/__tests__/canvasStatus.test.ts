import { RUN_STATUSES } from '../status';
import { canvasDotClass, canvasEdgeDone, canvasRingClass, canvasStatusLabel, fromLegacyCanvasStatus } from '../canvasStatus';

describe('canvas node status styling', () => {
  it('has no styling for a node that has not started (undefined / queued)', () => {
    expect(canvasRingClass(undefined)).toBe('');
    expect(canvasDotClass(undefined)).toBeNull();
    expect(canvasRingClass('queued')).toBe('');
    expect(canvasDotClass('queued')).toBeNull();
  });

  it('every other status has a dot and a ring, and waiting/skipped/interrupted are distinct from running', () => {
    const dots = new Set<string>();
    for (const s of RUN_STATUSES.filter((x) => x !== 'queued')) {
      const dot = canvasDotClass(s);
      expect(dot).toEqual(expect.any(String));
      expect(canvasRingClass(s)).toEqual(expect.any(String));
      dots.add(`${s}:${dot}`);
    }
    expect(canvasDotClass('waiting')).not.toBe(canvasDotClass('running'));
    expect(canvasDotClass('skipped')).not.toBe(canvasDotClass('completed'));
    expect(canvasDotClass('interrupted')).not.toBe(canvasDotClass('failed'));
  });

  it('waiting reads amber and completed reads green, failed red', () => {
    expect(canvasDotClass('waiting')).toContain('amber');
    expect(canvasDotClass('completed')).toContain('green');
    expect(canvasDotClass('failed')).toContain('red');
  });

  it('labels come from the shared enum', () => {
    expect(canvasStatusLabel('waiting')).toBe('Waiting');
    expect(canvasStatusLabel(undefined)).toBeNull();
  });

  it('an edge is done once its source completed or was skipped over', () => {
    expect(canvasEdgeDone('completed')).toBe(true);
    expect(canvasEdgeDone('running')).toBe(false);
    expect(canvasEdgeDone(undefined)).toBe(false);
  });

  it('maps the old canvas words onto the shared enum', () => {
    expect(fromLegacyCanvasStatus('complete')).toBe('completed');
    expect(fromLegacyCanvasStatus('error')).toBe('failed');
    expect(fromLegacyCanvasStatus('pending')).toBe('queued');
    expect(fromLegacyCanvasStatus('running')).toBe('running');
  });
});
