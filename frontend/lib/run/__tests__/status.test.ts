import { readFileSync } from 'fs';
import path from 'path';
import {
  RUN_STATUSES,
  engineTaskStatus,
  isActive,
  isTerminal,
  normalizeStatus,
  runStatusLabel,
} from '../status';

// Shared with backend/app/services/run_status.py (plan 003, U2/U23): both sides assert this file.
const fixture = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../../../backend/tests/fixtures/run_status_map.json'), 'utf8'),
) as {
  statuses: string[];
  legacy: Record<string, string>;
  engine_task: Record<string, string>;
  engine_outcome: Record<string, string>;
  unknown: string;
};

describe('run status', () => {
  it('has exactly the nine fixture statuses', () => {
    expect([...RUN_STATUSES]).toEqual(fixture.statuses);
  });

  it('maps every legacy value as the shared fixture does', () => {
    for (const [legacy, expected] of Object.entries(fixture.legacy)) {
      expect(normalizeStatus(legacy)).toBe(expected);
    }
  });

  it('maps engine task states as the shared fixture does', () => {
    for (const [status, expected] of Object.entries(fixture.engine_task)) {
      expect(engineTaskStatus(status)).toBe(expected);
    }
    for (const [outcome, expected] of Object.entries(fixture.engine_outcome)) {
      expect(engineTaskStatus(outcome)).toBe(expected);
    }
  });

  it('round-trips: every enum value normalizes to itself', () => {
    for (const s of RUN_STATUSES) expect(normalizeStatus(s)).toBe(s);
  });

  it('maps unknown or non-string values to the fixture fallback', () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    expect(normalizeStatus('bogus')).toBe(fixture.unknown);
    expect(normalizeStatus(undefined)).toBe(fixture.unknown);
    expect(normalizeStatus(7)).toBe(fixture.unknown);
    expect(engineTaskStatus('bogus')).toBe(fixture.unknown);
    warn.mockRestore();
  });

  it('is case and whitespace tolerant like the backend', () => {
    expect(normalizeStatus(' Running ')).toBe('running');
  });

  it('classifies terminal and active statuses and labels all of them', () => {
    expect(RUN_STATUSES.filter(isTerminal)).toEqual(['completed', 'failed', 'cancelled', 'skipped', 'interrupted']);
    expect(RUN_STATUSES.filter(isActive)).toEqual(['queued', 'running', 'waiting', 'resuming']);
    for (const s of RUN_STATUSES) expect(runStatusLabel(s).length).toBeGreaterThan(0);
  });
});
