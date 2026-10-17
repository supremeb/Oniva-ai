/**
 * Canonical form and hash of a tool call's final arguments (Approvals & Policy
 * plan, KTD2). The backend binds an approval to this hash and the execution
 * guard recomputes it, so the engine and Python must agree byte for byte
 * (`backend/app/services/tool_policy.py::canonical_args_hash`; the shared
 * vectors in `test/support/args-hash-vectors.json` keep them in step).
 *
 * The canonical form is compact JSON with object keys sorted by UTF-16 code
 * unit at every depth and arrays in order. The hash is the lowercase sha256
 * hex of its UTF-8 bytes. It is computed over the UNREDACTED arguments: a
 * redacted copy is what leaves the process in the request body, never the
 * input to the hash.
 */
import { createHash } from "node:crypto";
import type { JsonValue } from "../core/json.js";

/** Compact JSON with sorted object keys. */
export function canonicalJson(value: JsonValue): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map((v) => canonicalJson(v)).join(",")}]`;
  const keys = Object.keys(value)
    .filter((k) => value[k] !== undefined)
    .sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k] as JsonValue)}`).join(",")}}`;
}

/** sha256 hex of {@link canonicalJson}. */
export function argsHash(value: JsonValue): string {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}
