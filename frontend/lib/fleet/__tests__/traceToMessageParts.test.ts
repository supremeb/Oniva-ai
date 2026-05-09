import { traceToMessageParts } from '../traceToMessageParts';

/** Minimal fixture builder — only the fields the function actually reads */
function makeStep(overrides: Record<string, unknown> = {}) {
  return {
    step_id: 'step-1',
    status: 'completed' as const,
    ...overrides,
  } as any;
}

describe('traceToMessageParts', () => {
  // ── Tool steps ───────────────────────────────────────────────────────

  describe('tool step WITH reasoning', () => {
    it('emits a thinking part followed by a tool_call part (2 parts total)', () => {
      const { parts, toolCallMeta } = traceToMessageParts([
        makeStep({
          step_id: 't1',
          node_type: 'tool',
          node_id: 'search_web',
          status: 'completed',
          reasoning: 'I should search the web',
          input: { q: 'test' },
          output: { results: [] },
          duration_ms: 150,
          topic: 'web search',
        }),
      ]);

      expect(parts).toHaveLength(2);

      // First part: thinking
      expect(parts[0]).toMatchObject({
        type: 'thinking',
        variant: 'accordion',
        thinkingContent: 'I should search the web',
        isThinking: false,
      });

      // Second part: tool_call
      expect(parts[1]).toMatchObject({
        type: 'tool_call',
        id: 't1',
        toolName: 'search_web',
        status: 'success',
        args: { q: 'test' },
        result: { results: [] },
        durationMs: 150,
      });

      // toolCallMeta entry
      expect(toolCallMeta.size).toBe(1);
      const meta = toolCallMeta.get('t1');
      expect(meta).toBeDefined();
      expect(meta!.label).toBe('web search');
      expect(meta!.description).toBe('I should search the web');
      expect(meta!.sources).toEqual([]);
    });
  });

  describe('tool step WITHOUT reasoning', () => {
    it('emits only a tool_call part (1 part)', () => {
      const { parts } = traceToMessageParts([
        makeStep({
          step_id: 't2',
          node_type: 'tool',
          node_id: 'run_code',
          status: 'failed',
          input: { code: 'x' },
          error: 'syntax error',
        }),
      ]);

      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({
        type: 'tool_call',
        id: 't2',
        toolName: 'run_code',
        status: 'error',
        error: 'syntax error',
      });
    });
  });

  // ── LLM steps ────────────────────────────────────────────────────────

  describe('llm step', () => {
    it('emits a thinking part with isThinking=true when status is running', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'llm', status: 'running', reasoning: 'Analyzing data' }),
      ]);

      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({
        type: 'thinking',
        variant: 'accordion',
        thinkingContent: 'Analyzing data',
        isThinking: true,
      });
    });

    it('emits a thinking part with isThinking=false when status is completed', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'llm', status: 'completed', reasoning: 'Done thinking' }),
      ]);

      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({
        type: 'thinking',
        isThinking: false,
        thinkingContent: 'Done thinking',
      });
    });

    it('defaults thinkingContent to "Thinking..." when reasoning is absent', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'llm', status: 'running' }),
      ]);

      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({
        type: 'thinking',
        thinkingContent: 'Thinking...',
      });
    });
  });

  // ── Start steps ──────────────────────────────────────────────────────

  describe('start step', () => {
    it('emits a text part with "Agent started" when reasoning is absent', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'start' }),
      ]);

      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({ type: 'text', content: 'Agent started' });
    });

    it('uses step.reasoning as content when provided', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'start', reasoning: 'Initializing workflow' }),
      ]);

      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({ type: 'text', content: 'Initializing workflow' });
    });
  });

  // ── Generic / other steps ────────────────────────────────────────────

  describe('generic step', () => {
    it('emits a text part when reasoning is present', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'decision', reasoning: 'Choosing path A' }),
      ]);

      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({ type: 'text', content: 'Choosing path A' });
    });

    it('falls back to JSON.stringify(output) when reasoning is absent', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'decision', output: { chosen: 'A' } }),
      ]);

      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({
        type: 'text',
        content: JSON.stringify({ chosen: 'A' }),
      });
    });

    it('emits 0 parts when both reasoning and output are absent', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'decision' }),
      ]);

      expect(parts).toHaveLength(0);
    });

    it('emits 0 parts when reasoning is empty string and output is undefined', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'other', reasoning: '', output: undefined }),
      ]);

      expect(parts).toHaveLength(0);
    });
  });

  // ── mapToolStatus coercions ──────────────────────────────────────────

  describe('mapToolStatus coercions (via tool steps)', () => {
    const statuses: Array<{ input: string; expected: string }> = [
      { input: 'completed', expected: 'success' },
      { input: 'failed', expected: 'error' },
      { input: 'running', expected: 'running' },
      { input: 'pending', expected: 'pending' },
      { input: 'skipped', expected: 'success' },
    ];

    it.each(statuses)(
      'maps "$input" → "$expected"',
      ({ input, expected }) => {
        const { parts } = traceToMessageParts([
          makeStep({ node_type: 'tool', node_id: 'x', status: input }),
        ]);

        expect(parts[0]).toMatchObject({ type: 'tool_call', status: expected });
      },
    );

    it('maps unknown status to "pending"', () => {
      const { parts } = traceToMessageParts([
        makeStep({ node_type: 'tool', node_id: 'x', status: 'some_unknown_value' }),
      ]);

      expect(parts[0]).toMatchObject({ type: 'tool_call', status: 'pending' });
    });
  });

  // ── toolCallMeta ─────────────────────────────────────────────────────

  describe('toolCallMeta', () => {
    it('has one entry per tool step, keyed by step_id', () => {
      const { toolCallMeta } = traceToMessageParts([
        makeStep({ step_id: 'a', node_type: 'tool', node_id: 'tool_a', status: 'completed' }),
        makeStep({ step_id: 'b', node_type: 'tool', node_id: 'tool_b', status: 'failed' }),
        makeStep({ step_id: 'c', node_type: 'llm', status: 'completed' }),
      ]);

      expect(toolCallMeta.size).toBe(2);
      expect(toolCallMeta.has('a')).toBe(true);
      expect(toolCallMeta.has('b')).toBe(true);
      expect(toolCallMeta.has('c')).toBe(false);
    });

    it('populates label from topic, falling back to search_query', () => {
      const { toolCallMeta } = traceToMessageParts([
        makeStep({ step_id: 'q1', node_type: 'tool', node_id: 't', status: 'completed', search_query: 'fallback query' }),
      ]);

      expect(toolCallMeta.get('q1')!.label).toBe('fallback query');
    });

    it('prefers topic over search_query for label', () => {
      const { toolCallMeta } = traceToMessageParts([
        makeStep({ step_id: 'q2', node_type: 'tool', node_id: 't', status: 'completed', topic: 'main topic', search_query: 'query' }),
      ]);

      expect(toolCallMeta.get('q2')!.label).toBe('main topic');
    });
  });

  // ── ID fallback ──────────────────────────────────────────────────────

  describe('step_id fallback', () => {
    it('generates id as "trace-{index}" when step_id is missing', () => {
      const { parts } = traceToMessageParts([
        makeStep({ step_id: undefined, node_type: 'tool', node_id: 'x', status: 'completed' }),
      ]);

      expect(parts[0]).toMatchObject({ type: 'tool_call', id: 'trace-0' });
    });
  });

  // ── Empty input ──────────────────────────────────────────────────────

  describe('empty input', () => {
    it('returns empty parts and empty toolCallMeta for empty steps array', () => {
      const { parts, toolCallMeta } = traceToMessageParts([]);

      expect(parts).toHaveLength(0);
      expect(toolCallMeta.size).toBe(0);
    });
  });

  // ── node_id fallback for tool name ───────────────────────────────────

  describe('tool name fallback', () => {
    it('uses "tool" as toolName when node_id is absent', () => {
      const { parts, toolCallMeta } = traceToMessageParts([
        makeStep({ step_id: 'f1', node_type: 'tool', status: 'completed' }),
      ]);

      expect(parts[0]).toMatchObject({ type: 'tool_call', toolName: 'tool' });
      expect(toolCallMeta.get('f1')!.toolName).toBe('tool');
    });
  });
});
