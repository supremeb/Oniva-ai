import { describe, expect, it } from "vitest";
import type { JsonValue } from "../../src/core/json.js";
import { maskSecrets, REDACTED, redactSecretsInText } from "../../src/worker/redact.js";

const target = { record: "entry", kind: "tool_result", field: "payload" } as const;

describe("maskSecrets", () => {
  it("masks bearer tokens and API-key shapes in text", () => {
    const cases: Array<[string, string]> = [
      [
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123.def456",
        `Authorization: Bearer ${REDACTED}`,
      ],
      ["key sk-ant-api03-AbCdEf0123456789xyz end", `key ${REDACTED} end`],
      ["OPENAI=sk-proj-0123456789abcdefABCDEF", `OPENAI=${REDACTED}`],
      ["gh ghp_0123456789abcdefghijABCDEFGHIJ", `gh ${REDACTED}`],
      ["slack xoxb-1234567890-abcdefghij", `slack ${REDACTED}`],
      ["aws AKIAIOSFODNN7EXAMPLE", `aws ${REDACTED}`],
      ["api_key=4f9a8b7c6d5e4f3a2b1c", `api_key=${REDACTED}`],
      ['{"client_secret": "s3cr3t-value-here"}', `{"client_secret": "${REDACTED}"}`],
      ["password: hunter2hunter2", `password: ${REDACTED}`],
    ];
    for (const [input, expected] of cases) expect(redactSecretsInText(input)).toBe(expected);
  });

  it("masks PEM private key blocks", () => {
    const pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAK\n-----END RSA PRIVATE KEY-----";
    expect(redactSecretsInText(`k=${pem}!`)).toBe(
      `k=-----BEGIN RSA PRIVATE KEY-----${REDACTED}-----END RSA PRIVATE KEY-----!`,
    );
  });

  it("leaves ordinary content alone", () => {
    const keep = [
      "commit 4b825dc642cb6eb9a060e54bf8d69288fbee4904",
      "sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "conversation_0192f0c2-7a3e-7c3b-9a52-0d5f1c2b3a4d",
      "bearer instruments and bearer responsibilities",
      "the task-sk-1 is short",
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk",
      "tokens: 12345678",
    ];
    for (const text of keep) expect(redactSecretsInText(text)).toBe(text);
  });

  it("masks string values under secret-named keys, recursively", () => {
    const value: JsonValue = {
      tool: "http",
      args: {
        headers: { Authorization: "Basic dXNlcjpwYXNz", "X-Api-Key": "abc" },
        apiKey: "short",
        nextToken: "page-2",
        password: "",
        list: [{ access_token: "t" }, "sk-ant-0123456789abcdefghij"],
        count: 3,
      },
    };
    expect(maskSecrets(value, target)).toEqual({
      tool: "http",
      args: {
        headers: { Authorization: REDACTED, "X-Api-Key": REDACTED },
        apiKey: REDACTED,
        nextToken: "page-2",
        password: "",
        list: [{ access_token: REDACTED }, REDACTED],
        count: 3,
      },
    });
  });

  it("is deterministic and idempotent", () => {
    const value: JsonValue = {
      text: "Bearer abcdefghijklmnop1234 and sk-0123456789abcdefghij and secret=abcdefgh12345",
      nested: { password: "p4ssw0rd!", note: "api-key: QWERTYUIOP123456" },
      pem: "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----",
    };
    const once = maskSecrets(structuredClone(value), target);
    expect(maskSecrets(structuredClone(value), target)).toEqual(once);
    expect(maskSecrets(structuredClone(once), target)).toEqual(once);
    expect(JSON.stringify(once)).not.toMatch(/abcdefghijklmnop1234|0123456789abcdefghij|p4ssw0rd/);
  });
});
