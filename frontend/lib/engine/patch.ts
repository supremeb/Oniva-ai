/**
 * RFC 6902 subset (add / remove / replace) matching engine/src/watch/patch.ts.
 * Pure and immutable: the input is never modified and subtrees the patch does
 * not touch keep their identity (so memoized renders stay cheap).
 */
import type { JsonPatchOp, JsonValue } from './types';

export class InvalidPatch extends Error {
  constructor(
    readonly path: string,
    reason: string
  ) {
    super(`invalid patch at "${path}": ${reason}`);
    this.name = 'InvalidPatch';
  }
}

export const escapePointerToken = (t: string): string => t.replace(/~/g, '~0').replace(/\//g, '~1');
const unescape = (t: string): string => t.replace(/~1/g, '/').replace(/~0/g, '~');

type Obj = { [key: string]: JsonValue };
const isObject = (v: JsonValue | undefined): v is Obj =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

function parsePointer(path: string): string[] {
  if (path === '') return [];
  if (!path.startsWith('/')) throw new InvalidPatch(path, "a pointer must start with '/'");
  return path.slice(1).split('/').map(unescape);
}

function arrayIndex(path: string, token: string, length: number, allowEnd: boolean): number {
  if (allowEnd && token === '-') return length;
  if (!/^(0|[1-9][0-9]*)$/.test(token)) throw new InvalidPatch(path, `bad array index ${token}`);
  const i = Number(token);
  if (i > length || (!allowEnd && i === length)) {
    throw new InvalidPatch(path, `array index ${i} out of range`);
  }
  return i;
}

function setMember(target: Obj, key: string, value: JsonValue): Obj {
  const copy: Obj = {};
  for (const k of Object.keys(target)) {
    Object.defineProperty(copy, k, {
      value: target[k],
      writable: true,
      enumerable: true,
      configurable: true,
    });
  }
  Object.defineProperty(copy, key, { value, writable: true, enumerable: true, configurable: true });
  return copy;
}

function applyAt(
  node: JsonValue,
  tokens: string[],
  depth: number,
  op: JsonPatchOp
): JsonValue {
  const token = tokens[depth] as string;
  const last = depth === tokens.length - 1;
  if (Array.isArray(node)) {
    if (last) {
      const i = arrayIndex(op.path, token, node.length, op.op === 'add');
      const copy = node.slice();
      if (op.op === 'add') copy.splice(i, 0, op.value);
      else if (op.op === 'replace') copy[i] = op.value;
      else copy.splice(i, 1);
      return copy;
    }
    const i = arrayIndex(op.path, token, node.length, false);
    const copy = node.slice();
    copy[i] = applyAt(node[i] as JsonValue, tokens, depth + 1, op);
    return copy;
  }
  if (isObject(node)) {
    const has = Object.hasOwn(node, token);
    if (last) {
      if (op.op !== 'add' && !has) throw new InvalidPatch(op.path, `missing ${token}`);
      if (op.op === 'remove') {
        const copy: Obj = {};
        for (const k of Object.keys(node)) if (k !== token) setOwn(copy, k, node[k] as JsonValue);
        return copy;
      }
      return setMember(node, token, op.value);
    }
    if (!has) throw new InvalidPatch(op.path, `missing ${token}`);
    return setMember(node, token, applyAt(node[token] as JsonValue, tokens, depth + 1, op));
  }
  throw new InvalidPatch(op.path, 'parent is not a container');
}

function setOwn(target: Obj, key: string, value: JsonValue): void {
  Object.defineProperty(target, key, { value, writable: true, enumerable: true, configurable: true });
}

export function applyJsonPatch<T extends JsonValue>(doc: T, ops: readonly JsonPatchOp[]): T {
  let root: JsonValue = doc;
  for (const op of ops) {
    const tokens = parsePointer(op.path);
    if (tokens.length === 0) {
      if (op.op === 'remove') throw new InvalidPatch(op.path, 'cannot remove the root');
      root = op.value;
      continue;
    }
    root = applyAt(root, tokens, 0, op);
  }
  return root as T;
}
