/**
 * The production worker's default host redaction (R20): masks credentials in
 * every stored JSON payload before it is committed or published.
 *
 * Deliberately conservative, so ordinary content (ids, git SHAs, hashes,
 * base64 data) survives untouched. It masks only:
 *
 * - bearer credentials: `Bearer <token>` keeps the scheme, masks the token;
 * - well-known API-key shapes: `sk-…` (OpenAI / Anthropic style), GitHub
 *   (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`), Slack (`xox?-`),
 *   AWS access key ids (`AKIA…`) and PEM private key blocks;
 * - long values assigned to a secret-named key in text
 *   (`api_key=…`, `"client_secret": "…"`, `password: …`);
 * - string values under a secret-named JSON object key (`apiKey`,
 *   `password`, `client_secret`, `access_token`, `authorization`, …).
 *
 * Masking replaces with {@link REDACTED}, which no pattern matches, so the
 * function is deterministic and idempotent as `Redact` requires.
 */
import type { JsonValue } from "../core/json.js";
import type { Redact } from "../session/transaction.js";

export const REDACTED = "[REDACTED]";

/** Object keys whose string values are always masked (compared without `_`/`-`, any case). */
const SECRET_KEYS = new Set([
  "apikey",
  "xapikey",
  "secret",
  "clientsecret",
  "secretkey",
  "secretaccesskey",
  "password",
  "passwd",
  "accesstoken",
  "refreshtoken",
  "idtoken",
  "authtoken",
  "bearertoken",
  "sessiontoken",
  "privatekey",
  "authorization",
  "proxyauthorization",
]);

const normalizeKey = (key: string): string => key.replace(/[-_\s]/g, "").toLowerCase();

const TOKEN_PATTERNS: ReadonlyArray<[RegExp, string]> = [
  // PEM private keys (whole block).
  [
    /-----BEGIN ((?:[A-Z]+ )*PRIVATE KEY)-----[\s\S]*?-----END \1-----/g,
    `-----BEGIN $1-----${REDACTED}-----END $1-----`,
  ],
  // Authorization: Bearer <token>
  // (16+ token characters including a digit, so prose like
  // "bearer instruments" is left alone).
  [/\b(Bearer)\s+(?=[A-Za-z\-._~+/]*[0-9])[A-Za-z0-9\-._~+/]{16,}=*/gi, `$1 ${REDACTED}`],
  // sk-... / sk-ant-... / sk-proj-...
  [/\bsk-[A-Za-z0-9_-]{16,}/g, REDACTED],
  // GitHub tokens.
  [/\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})/g, REDACTED],
  // Slack tokens.
  [/\bxox[abposr]-[A-Za-z0-9-]{10,}/g, REDACTED],
  // AWS access key ids.
  [/\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g, REDACTED],
  // secret_name = value / "secret_name": "value" (value of 8+ token characters).
  [
    /\b((?:api[_-]?key|x-api-key|client[_-]?secret|secret[_-]?key|secret|password|passwd|access[_-]?token|refresh[_-]?token|auth[_-]?token)["']?\s*[:=]\s*["']?)[A-Za-z0-9\-._~+/]{8,}=*/gi,
    `$1${REDACTED}`,
  ],
];

/** Mask credentials in one string. Idempotent. */
export function redactSecretsInText(text: string): string {
  let out = text;
  for (const [pattern, replacement] of TOKEN_PATTERNS) out = out.replace(pattern, replacement);
  return out;
}

function redactValue(value: JsonValue): JsonValue {
  if (typeof value === "string") return redactSecretsInText(value);
  if (Array.isArray(value)) return value.map(redactValue);
  if (value !== null && typeof value === "object") {
    const out: Record<string, JsonValue> = {};
    for (const [key, v] of Object.entries(value)) {
      out[key] =
        typeof v === "string" && v.length > 0 && SECRET_KEYS.has(normalizeKey(key))
          ? REDACTED
          : redactValue(v as JsonValue);
    }
    return out;
  }
  return value;
}

/** The default secret-masking redactor for production workers. */
export const maskSecrets: Redact = (value) => redactValue(value);
