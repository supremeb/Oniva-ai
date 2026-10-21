import {
  fetchProjectMemory,
  putMemoryFile,
  deleteMemoryFile,
  patchMemoryBullet,
  deleteMemoryBullet,
  approvePendingMemory,
  rejectPendingMemory,
  exportProjectMemory,
  purgeProjectMemory,
} from '../project-memory';
import { apiFetch } from '../index';

jest.mock('../index', () => ({ apiFetch: jest.fn().mockResolvedValue({}) }));

const mock = apiFetch as jest.Mock;
const B = '/api/v1/projects/p1/memory';

beforeEach(() => mock.mockClear());

describe('project-memory api', () => {
  it('fetches the overview', async () => {
    await fetchProjectMemory('p1');
    expect(mock).toHaveBeenCalledWith(B);
  });

  it('PUTs a file with expected_version, encoding path segments', async () => {
    await putMemoryFile('p1', 'topics/my notes.md', 'hi', 3);
    const [url, opts] = mock.mock.calls[0];
    expect(url).toBe(`${B}/files/topics/my%20notes.md`);
    expect(opts.method).toBe('PUT');
    expect(JSON.parse(opts.body)).toEqual({ content: 'hi', expected_version: 3 });
  });

  it('omits expected_version for a new file', async () => {
    await putMemoryFile('p1', 'core.md', 'x');
    expect(JSON.parse(mock.mock.calls[0][1].body)).toEqual({ content: 'x' });
  });

  it('DELETEs a file with the version as a query param', async () => {
    await deleteMemoryFile('p1', 'core.md', 2);
    expect(mock).toHaveBeenCalledWith(`${B}/files/core.md?expected_version=2`, { method: 'DELETE' });
    await deleteMemoryFile('p1', 'core.md');
    expect(mock).toHaveBeenLastCalledWith(`${B}/files/core.md`, { method: 'DELETE' });
  });

  it('PATCHes and DELETEs bullets', async () => {
    await patchMemoryBullet('p1', 'b1', { text: 'new' });
    expect(mock.mock.calls[0][0]).toBe(`${B}/bullets/b1`);
    expect(mock.mock.calls[0][1].method).toBe('PATCH');
    expect(JSON.parse(mock.mock.calls[0][1].body)).toEqual({ text: 'new' });
    await deleteMemoryBullet('p1', 'b1');
    expect(mock).toHaveBeenLastCalledWith(`${B}/bullets/b1`, { method: 'DELETE' });
  });

  it('approves and rejects pending items by id', async () => {
    await approvePendingMemory('p1', 'i1');
    expect(mock).toHaveBeenLastCalledWith(`${B}/pending/i1/approve`, { method: 'POST' });
    await rejectPendingMemory('p1', 'i1');
    expect(mock).toHaveBeenLastCalledWith(`${B}/pending/i1/reject`, { method: 'POST' });
  });

  it('exports with POST and purges with DELETE', async () => {
    await exportProjectMemory('p1');
    expect(mock).toHaveBeenLastCalledWith(`${B}/export`, { method: 'POST' });
    await purgeProjectMemory('p1');
    expect(mock).toHaveBeenLastCalledWith(B, { method: 'DELETE' });
  });
});
