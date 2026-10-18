import { pollIntervalFor, POLL_ACTIVE_MS, POLL_PARKED_MS } from '../useWorkflowLogs';

describe('pollIntervalFor', () => {
  it('polls fast while any run is moving, slowly when every unfinished run is waiting, never when all are finished', () => {
    expect(pollIntervalFor(['running'])).toBe(POLL_ACTIVE_MS);
    expect(pollIntervalFor(['queued'])).toBe(POLL_ACTIVE_MS);
    expect(pollIntervalFor(['resuming', 'waiting'])).toBe(POLL_ACTIVE_MS);
    expect(pollIntervalFor(['waiting', 'completed'])).toBe(POLL_PARKED_MS);
    expect(pollIntervalFor(['waiting'])).toBe(POLL_PARKED_MS);
    expect(pollIntervalFor(['completed', 'failed'])).toBe(false);
    expect(pollIntervalFor([])).toBe(false);
  });
});
