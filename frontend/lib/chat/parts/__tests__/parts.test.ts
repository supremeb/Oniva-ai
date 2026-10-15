import {
  buildErrorPart,
  buildFilePart,
  buildRunningToolCallPart,
  buildSourcePart,
  buildTextPart,
  buildThinkingPart,
  buildToolCallPart,
  deriveFileParts,
  deriveSourceParts,
  parseToolResultContent,
  stripFunctionCallMarkup,
} from '..';

describe('part builders', () => {
  it('text: isStreaming only when given', () => {
    expect(buildTextPart('a')).toEqual({ type: 'text', content: 'a' });
    expect(Object.keys(buildTextPart('a'))).toEqual(['type', 'content']);
    expect(buildTextPart('a', false)).toEqual({ type: 'text', content: 'a', isStreaming: false });
  });

  it('thinking: accordion with optional showElapsed', () => {
    expect(buildThinkingPart({ content: 'x', isThinking: true, showElapsed: true })).toEqual({
      type: 'thinking', variant: 'accordion', thinkingContent: 'x', isThinking: true, showElapsed: true,
    });
    expect('showElapsed' in buildThinkingPart({ content: 'x', isThinking: false })).toBe(false);
  });

  it('tool call: drops undefined fields; running part carries the label as its first step', () => {
    expect(Object.keys(buildToolCallPart({ id: 'c', toolName: 't', status: 'success', args: undefined }))).toEqual(['type', 'id', 'toolName', 'status']);
    expect(buildRunningToolCallPart({ id: 'c', toolName: 't', args: { a: 1 }, label: 'Doing' })).toEqual({
      type: 'tool_call', id: 'c', toolName: 't', status: 'running', args: { a: 1 },
      steps: [{ id: 'step-c', label: 'Doing', status: 'active' }],
    });
    expect('steps' in buildRunningToolCallPart({ id: 'c', toolName: 't' })).toBe(false);
  });

  it('source: sourceType defaults from the url; citation number is optional', () => {
    expect(buildSourcePart({ title: 'T', url: 'https://x', snippet: 's' })).toEqual({ type: 'source', sourceType: 'url', title: 'T', url: 'https://x', description: 's' });
    expect(buildSourcePart({ title: 'T', url: '/kb/1', citationNumber: 2 })).toMatchObject({ sourceType: 'document', citationNumber: 2 });
  });

  it('file and error', () => {
    expect(buildFilePart({})).toEqual({ type: 'file', filename: 'untitled' });
    expect(buildErrorPart({ message: 'm', retryable: false })).toEqual({ type: 'error', message: 'm', retryable: false });
  });

  it('stripFunctionCallMarkup removes leaked tool markup', () => {
    expect(stripFunctionCallMarkup('hi <function_calls><invoke name="x"></invoke></function_calls> there')).toBe('hi  there');
    expect(stripFunctionCallMarkup('plain')).toBe('plain');
  });
});

describe('result heuristics (ported from backend chat.py)', () => {
  it('parseToolResultContent', () => {
    expect(parseToolResultContent('{"a":1}')).toEqual({ a: 1 });
    expect(parseToolResultContent('{broken')).toBe('{broken');
    expect(parseToolResultContent('text')).toBe('text');
  });

  it('web_search yields url sources, skipping rows without a url', () => {
    const p = deriveSourceParts('web_search', { results: [{ url: 'https://a', title: 'A', snippet: 's' }, { title: 'no url' }, { url: 'https://b' }] });
    expect(p).toEqual([
      { type: 'source', sourceType: 'url', title: 'A', url: 'https://a', description: 's' },
      { type: 'source', sourceType: 'url', title: 'Web result', url: 'https://b', description: '' },
    ]);
  });

  it('files: an unrelated tool without file keys yields nothing; keys alone are enough', () => {
    expect(deriveFileParts('calc', 'c', { success: true, value: 1 })).toEqual([]);
    expect(deriveFileParts('anything', 'c', { success: true, filename: 'x.csv', download_url: '/d' })).toEqual([
      { type: 'file', filename: 'x.csv', fileType: 'text/csv', downloadUrl: '/d', toolCallId: 'c' },
    ]);
    expect(deriveFileParts('write_file', 'c', { success: false, filename: 'x.csv' })).toEqual([]);
    expect(deriveFileParts('write_file', 'c', 'text')).toEqual([]);
  });
});
