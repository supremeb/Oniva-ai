/**
 * A small JSON Schema subset (KTD13) for a subagent's typed result.
 *
 * Supported keywords: `type` (`string`, `number`, `integer`, `boolean`,
 * `null`, `object`, `array`), `properties`, `required`, `items` (one schema),
 * `enum`, `minItems`, `maxItems`, `minLength`, `maxLength`, `minimum`,
 * `maximum`; `title` and `description` are accepted as annotations. Any other
 * keyword is rejected when the schema is parsed, never ignored, so a schema
 * cannot promise a constraint that is not enforced. Objects accept properties
 * that `properties` does not name. A schema is capped at 16 KiB and 8 levels.
 */
import type { JsonObject, JsonValue } from "../core/json.js";

export const MAX_OUTPUT_SCHEMA_BYTES = 16 * 1024;
export const MAX_OUTPUT_SCHEMA_DEPTH = 8;

const TYPES = ["string", "number", "integer", "boolean", "null", "object", "array"] as const;
const KEYWORDS: ReadonlySet<string> = new Set([
  "type",
  "properties",
  "required",
  "items",
  "enum",
  "minItems",
  "maxItems",
  "minLength",
  "maxLength",
  "minimum",
  "maximum",
  "title",
  "description",
]);

const isObject = (v: unknown): v is JsonObject =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Check that `schema` uses only the supported subset; returns the problems found (none: valid). */
export function schemaProblems(schema: unknown): string[] {
  const out: string[] = [];
  let bytes: number;
  try {
    bytes = new TextEncoder().encode(JSON.stringify(schema) ?? "").length;
  } catch {
    return ["outputSchema is not JSON"];
  }
  if (bytes > MAX_OUTPUT_SCHEMA_BYTES) {
    return [`outputSchema is larger than ${MAX_OUTPUT_SCHEMA_BYTES} bytes`];
  }
  const walk = (s: unknown, path: string, depth: number): void => {
    if (!isObject(s)) {
      out.push(`${path}: a schema must be an object`);
      return;
    }
    if (depth > MAX_OUTPUT_SCHEMA_DEPTH) {
      out.push(`${path}: deeper than ${MAX_OUTPUT_SCHEMA_DEPTH} levels`);
      return;
    }
    for (const key of Object.keys(s)) {
      if (!KEYWORDS.has(key)) out.push(`${path}: unsupported keyword "${key}"`);
    }
    if (s.type !== undefined && !(TYPES as readonly unknown[]).includes(s.type)) {
      out.push(`${path}.type: must be one of ${TYPES.join(", ")}`);
    }
    if (s.properties !== undefined) {
      if (!isObject(s.properties)) out.push(`${path}.properties: must be an object`);
      else {
        for (const [k, sub] of Object.entries(s.properties)) {
          walk(sub, `${path}.properties.${k}`, depth + 1);
        }
      }
    }
    if (s.required !== undefined) {
      if (!Array.isArray(s.required) || !s.required.every((r) => typeof r === "string")) {
        out.push(`${path}.required: must be an array of strings`);
      }
    }
    if (s.items !== undefined) walk(s.items, `${path}.items`, depth + 1);
    if (s.enum !== undefined && (!Array.isArray(s.enum) || s.enum.length === 0)) {
      out.push(`${path}.enum: must be a non-empty array`);
    }
    for (const k of ["minItems", "maxItems", "minLength", "maxLength"]) {
      const v = s[k];
      if (v !== undefined && !(Number.isSafeInteger(v) && (v as number) >= 0)) {
        out.push(`${path}.${k}: must be a non-negative integer`);
      }
    }
    for (const k of ["minimum", "maximum"]) {
      const v = s[k];
      if (v !== undefined && !(typeof v === "number" && Number.isFinite(v))) {
        out.push(`${path}.${k}: must be a number`);
      }
    }
    for (const k of ["title", "description"]) {
      if (s[k] !== undefined && typeof s[k] !== "string")
        out.push(`${path}.${k}: must be a string`);
    }
  };
  walk(schema, "$", 1);
  return out;
}

function typeOf(v: JsonValue): (typeof TYPES)[number] {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  if (typeof v === "object") return "object";
  if (typeof v === "number") return Number.isInteger(v) ? "integer" : "number";
  return typeof v as "string" | "boolean";
}

function equal(a: JsonValue, b: JsonValue): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Violations of `value` against a schema already accepted by {@link schemaProblems}; empty means valid. */
export function validateAgainstSchema(schema: JsonObject, value: JsonValue): string[] {
  const out: string[] = [];
  const walk = (s: JsonObject, v: JsonValue, path: string): void => {
    const t = typeOf(v);
    if (s.type !== undefined) {
      const ok = s.type === t || (s.type === "number" && t === "integer");
      if (!ok) {
        out.push(`${path}: expected ${String(s.type)}, got ${t}`);
        return;
      }
    }
    if (Array.isArray(s.enum) && !s.enum.some((e) => equal(e as JsonValue, v))) {
      out.push(`${path}: must be one of ${JSON.stringify(s.enum)}`);
    }
    if (typeof v === "string") {
      if (typeof s.minLength === "number" && v.length < s.minLength) {
        out.push(`${path}: shorter than ${s.minLength} characters`);
      }
      if (typeof s.maxLength === "number" && v.length > s.maxLength) {
        out.push(`${path}: longer than ${s.maxLength} characters`);
      }
    }
    if (typeof v === "number") {
      if (typeof s.minimum === "number" && v < s.minimum) out.push(`${path}: below ${s.minimum}`);
      if (typeof s.maximum === "number" && v > s.maximum) out.push(`${path}: above ${s.maximum}`);
    }
    if (Array.isArray(v)) {
      if (typeof s.minItems === "number" && v.length < s.minItems) {
        out.push(`${path}: fewer than ${s.minItems} items`);
      }
      if (typeof s.maxItems === "number" && v.length > s.maxItems) {
        out.push(`${path}: more than ${s.maxItems} items`);
      }
      if (isObject(s.items)) {
        const items = s.items;
        for (const [i, item] of v.entries()) walk(items, item, `${path}[${i}]`);
      }
    }
    if (isObject(v)) {
      if (Array.isArray(s.required)) {
        for (const r of s.required) {
          if (typeof r === "string" && !Object.hasOwn(v, r)) {
            out.push(`${path}: missing required property "${r}"`);
          }
        }
      }
      if (isObject(s.properties)) {
        for (const [k, sub] of Object.entries(s.properties)) {
          const child = v[k];
          if (Object.hasOwn(v, k) && child !== undefined && isObject(sub)) {
            walk(sub, child, `${path}.${k}`);
          }
        }
      }
    }
  };
  walk(schema, value, "$");
  return out;
}

/** Instructions appended to a child's system prompt for a typed result. */
export function outputSchemaInstructions(schema: JsonObject): string {
  return [
    "When your task is complete, finish by calling the complete_task tool exactly once, with `result` set to a JSON value that matches this JSON Schema. Do not answer in plain text instead.",
    JSON.stringify(schema),
  ].join("\n\n");
}
