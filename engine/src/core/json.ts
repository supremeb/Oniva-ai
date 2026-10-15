/**
 * Strict JSON: plain data only. Rejects undefined, non-finite numbers,
 * functions, symbols, bigints, non-plain objects (Date, Map, class instances),
 * sparse arrays and cycles. Shared (acyclic) references are allowed.
 */
import { InvalidJson } from "./errors.js";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export function assertJson(value: unknown, rootPath = "$"): asserts value is JsonValue {
  walk(value, rootPath, new Set());
}

export function isJson(value: unknown): value is JsonValue {
  try {
    assertJson(value);
    return true;
  } catch (err) {
    if (err instanceof InvalidJson) return false;
    throw err;
  }
}

/** JSON.parse that reports failures as InvalidJson. */
export function parseJson(text: string): JsonValue {
  try {
    return JSON.parse(text) as JsonValue;
  } catch (err) {
    throw new InvalidJson("$", err instanceof Error ? err.message : "unparseable", { cause: err });
  }
}

/** Validate then serialize. */
export function stringifyJson(value: unknown): string {
  assertJson(value);
  return JSON.stringify(value);
}

/** UTF-8 byte length of the serialized value (validates first). */
export function jsonByteLength(value: unknown): number {
  return Buffer.byteLength(stringifyJson(value), "utf8");
}

function walk(value: unknown, path: string, ancestors: Set<object>): void {
  switch (typeof value) {
    case "string":
    case "boolean":
      return;
    case "number":
      if (!Number.isFinite(value)) throw new InvalidJson(path, `non-finite number ${value}`);
      return;
    case "undefined":
      throw new InvalidJson(path, "undefined");
    case "object":
      break;
    default:
      throw new InvalidJson(path, typeof value);
  }
  if (value === null) return;
  if (ancestors.has(value)) throw new InvalidJson(path, "cycle");
  ancestors.add(value);
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      const p = `${path}[${i}]`;
      if (!(i in value)) throw new InvalidJson(p, "sparse array hole");
      walk(value[i], p, ancestors);
    }
  } else {
    const proto = Object.getPrototypeOf(value);
    if (proto !== Object.prototype && proto !== null) {
      throw new InvalidJson(path, `non-plain object ${proto?.constructor?.name ?? "unknown"}`);
    }
    if (Object.getOwnPropertySymbols(value).length > 0) {
      throw new InvalidJson(path, "symbol keys");
    }
    for (const [k, v] of Object.entries(value)) {
      walk(
        v,
        `${path}${/^[A-Za-z_$][\w$]*$/.test(k) ? `.${k}` : `[${JSON.stringify(k)}]`}`,
        ancestors,
      );
    }
  }
  ancestors.delete(value);
}
