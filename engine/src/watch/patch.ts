/**
 * JSON Patch (RFC 6902) for watch frames: generation and application of the
 * `add`, `remove` and `replace` operations over strict JSON values.
 *
 * `diffJson` walks both values: objects are compared member by member,
 * arrays index by index (extra items are added at the end, surplus items
 * removed from the end, highest index first), and anything else that differs
 * is replaced. Applying the result to `before` yields a value deep-equal to
 * `after`.
 */
import { isDeepStrictEqual } from "node:util";
import type { JsonValue } from "../core/json.js";

export type JsonPatchOp =
  | { op: "add"; path: string; value: JsonValue }
  | { op: "replace"; path: string; value: JsonValue }
  | { op: "remove"; path: string };

/** A patch that does not apply to the document it was given. */
export class InvalidPatch extends Error {
  constructor(
    readonly path: string,
    reason: string,
  ) {
    super(`invalid patch at "${path}": ${reason}`);
    this.name = "InvalidPatch";
  }
}

/** Escape one JSON Pointer reference token (RFC 6901). */
export function escapePointerToken(token: string): string {
  return token.replace(/~/g, "~0").replace(/\//g, "~1");
}

function unescapePointerToken(token: string): string {
  return token.replace(/~1/g, "/").replace(/~0/g, "~");
}

type JsonObjectValue = { [key: string]: JsonValue };

function isObject(v: JsonValue): v is JsonObjectValue {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** The operations turning `before` into `after`, rooted at `basePath`. */
export function diffJson(before: JsonValue, after: JsonValue, basePath = ""): JsonPatchOp[] {
  const out: JsonPatchOp[] = [];
  diffInto(before, after, basePath, out);
  return out;
}

function diffInto(a: JsonValue, b: JsonValue, path: string, out: JsonPatchOp[]): void {
  if (isDeepStrictEqual(a, b)) return;
  if (isObject(a) && isObject(b)) {
    for (const key of Object.keys(a)) {
      if (!Object.hasOwn(b, key))
        out.push({ op: "remove", path: `${path}/${escapePointerToken(key)}` });
    }
    for (const [key, value] of Object.entries(b)) {
      const child = `${path}/${escapePointerToken(key)}`;
      if (Object.hasOwn(a, key)) diffInto(a[key] as JsonValue, value, child, out);
      else out.push({ op: "add", path: child, value: structuredClone(value) });
    }
    return;
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    const common = Math.min(a.length, b.length);
    for (let i = 0; i < common; i++)
      diffInto(a[i] as JsonValue, b[i] as JsonValue, `${path}/${i}`, out);
    for (let i = common; i < b.length; i++) {
      out.push({ op: "add", path: `${path}/${i}`, value: structuredClone(b[i] as JsonValue) });
    }
    for (let i = a.length - 1; i >= common; i--) out.push({ op: "remove", path: `${path}/${i}` });
    return;
  }
  out.push({ op: "replace", path, value: structuredClone(b) });
}

function parsePointer(path: string): string[] {
  if (path === "") return [];
  if (!path.startsWith("/")) throw new InvalidPatch(path, "a pointer must start with '/'");
  return path.slice(1).split("/").map(unescapePointerToken);
}

function arrayIndex(path: string, token: string, length: number, allowEnd: boolean): number {
  if (allowEnd && token === "-") return length;
  if (!/^(0|[1-9][0-9]*)$/.test(token)) throw new InvalidPatch(path, `bad array index ${token}`);
  const i = Number(token);
  if (i > length || (!allowEnd && i === length)) {
    throw new InvalidPatch(path, `array index ${i} out of range`);
  }
  return i;
}

/** Own-property write, safe for keys such as `__proto__`. */
function setMember(target: JsonObjectValue, key: string, value: JsonValue): void {
  Object.defineProperty(target, key, {
    value,
    writable: true,
    enumerable: true,
    configurable: true,
  });
}

/** Apply `ops` to a copy of `doc` and return it. `doc` is not modified. */
export function applyJsonPatch<T extends JsonValue>(doc: T, ops: readonly JsonPatchOp[]): T {
  let root: JsonValue = structuredClone(doc);
  for (const op of ops) {
    const tokens = parsePointer(op.path);
    if (tokens.length === 0) {
      if (op.op === "remove") throw new InvalidPatch(op.path, "cannot remove the root");
      root = structuredClone(op.value);
      continue;
    }
    let parent: JsonValue = root;
    for (const token of tokens.slice(0, -1)) {
      if (Array.isArray(parent)) {
        parent = parent[arrayIndex(op.path, token, parent.length, false)] as JsonValue;
      } else if (isObject(parent) && Object.hasOwn(parent, token)) {
        parent = parent[token] as JsonValue;
      } else {
        throw new InvalidPatch(op.path, `missing ${token}`);
      }
    }
    const last = tokens[tokens.length - 1] as string;
    if (Array.isArray(parent)) {
      const i = arrayIndex(op.path, last, parent.length, op.op === "add");
      if (op.op === "add") parent.splice(i, 0, structuredClone(op.value));
      else if (op.op === "replace") parent[i] = structuredClone(op.value);
      else parent.splice(i, 1);
    } else if (isObject(parent)) {
      if (op.op !== "add" && !Object.hasOwn(parent, last)) {
        throw new InvalidPatch(op.path, `missing ${last}`);
      }
      if (op.op === "remove") delete parent[last];
      else setMember(parent, last, structuredClone(op.value));
    } else {
      throw new InvalidPatch(op.path, "parent is not a container");
    }
  }
  return root as T;
}
