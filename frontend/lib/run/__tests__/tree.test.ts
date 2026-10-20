import { buildRunTree, type FlatRunNode } from '../tree';

const n = (id: string, parentId: string | null, status: FlatRunNode['status'] = 'running'): FlatRunNode => ({
  id,
  parentId,
  kind: 'node',
  label: id,
  status,
  startedAt: null,
  endedAt: null,
});

describe('buildRunTree', () => {
  it('nests children under parents in input order', () => {
    const [root] = buildRunTree([n('r', null), n('a', 'r'), n('b', 'r'), n('a1', 'a')]);
    expect(root.children.map((c) => c.id)).toEqual(['a', 'b']);
    expect(root.children[0].children.map((c) => c.id)).toEqual(['a1']);
  });

  it('keeps orphans as roots and survives a parent cycle', () => {
    expect(buildRunTree([n('x', 'missing')]).map((r) => r.id)).toEqual(['x']);
    const ids = buildRunTree([n('p', 'q'), n('q', 'p')]).map((r) => r.id);
    expect(ids).toEqual(['p']);
  });

  it('reuses unchanged subtrees from the previous tree', () => {
    const first = buildRunTree([n('r', null), n('a', 'r'), n('b', 'r')]);
    const second = buildRunTree([n('r', null), n('a', 'r'), n('b', 'r', 'completed')], first);
    expect(second[0]).not.toBe(first[0]);
    expect(second[0].children[0]).toBe(first[0].children[0]);
    expect(buildRunTree([n('r', null), n('a', 'r'), n('b', 'r')], first)[0]).toBe(first[0]);
  });
});
