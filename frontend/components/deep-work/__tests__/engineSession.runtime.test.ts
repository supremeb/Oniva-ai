import type { DeepWorkFullState } from '@/lib/stores/deep-work';
import { isEngineRuntimeSession } from '../engineSession';

const base = { session_id: 's', conversation_id: 'c', status: 'running', plan: null, todos: [], steps: [], workspace_files: [], summary: null, key_findings: [], sources: [], connectors: [], engine_phase: null, error: null, workers: null };
const fs = (over: Record<string, unknown>) => ({ ...base, is_engine: true, ...over }) as unknown as DeepWorkFullState;

describe('isEngineRuntimeSession', () => {
  it('trusts the explicit runtime over the engine_report heuristic', () => {
    // The API response model always serialises engine_report (null for engine runs): the old heuristic would say legacy.
    expect(isEngineRuntimeSession(fs({ runtime: 'engine', engine_report: null }))).toBe(true);
    expect(isEngineRuntimeSession(fs({ runtime: 'legacy' }))).toBe(false);
    expect(isEngineRuntimeSession(fs({ runtime: 'legacy', engine_report: '# r' }))).toBe(false);
  });

  it('falls back to the engine_report heuristic only when runtime is absent', () => {
    expect(isEngineRuntimeSession(fs({}))).toBe(true);
    expect(isEngineRuntimeSession(fs({ engine_report: null }))).toBe(false);
  });

  it('needs an engine session with a conversation id', () => {
    expect(isEngineRuntimeSession(null)).toBe(false);
    expect(isEngineRuntimeSession(fs({ runtime: 'engine', conversation_id: null }))).toBe(false);
    expect(isEngineRuntimeSession(fs({ runtime: 'engine', is_engine: false }))).toBe(false);
  });
});
