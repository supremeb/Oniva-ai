/**
 * A fake of Oniva's workflow node gateway, and the specification of its
 * contract (plan 2026-10-17-003, U11, KTD4).
 *
 * Each `[clause:<id>]` tag marks a behavior the Python backend must
 * reproduce; `backend/tests/internal/test_contract_conformance.py` (U13) reads
 * these tags and needs a conformance test for each. The payload shapes are the
 * committed U33 schemas and fixtures under `contracts/workflow-nodes/`; the
 * fake serves those fixtures unless a test scripts something else.
 *
 * Every request carries `Authorization: Bearer <service token>`; a wrong or
 * missing token is answered `401 { error: "unauthorized" }`. [clause:auth-401]
 *
 * Fencing. A request may carry `X-Engine-Fence: <token>` and `X-Engine-Session`
 * (the engine sends both while it holds a lease). A token lower than the
 * session's high-water mark is answered `409 { error: "fence_rejected",
 * highWater }` and has no effect [clause:fence-409]; any other token raises the
 * mark, on reads too [clause:fence-raise-on-read]. A token that is not an
 * integer is `400 { error: "invalid_fence" }`. [clause:fence-invalid-400]
 *
 * Node routes (all `POST`, JSON, keyed by `Idempotency-Key`):
 * - `/internal/workflows/runs/{runId}/nodes/{nodeId}/prepare` with body
 *   `{ contractVersion, runAttempt, nodeAttempt, nodeType }` → `200` with a
 *   `prepare.response` body (`final`, `llm`, `wait` or `fanout`).
 *   [clause:prepare-response-shape] The tenant and project are those of the
 *   run, never taken from the body. [clause:run-binding]
 * - `/internal/workflows/runs/{runId}/nodes/{nodeId}/finish` with a
 *   `finish.input` body → `200` with a `final` body. [clause:finish-returns-final]
 *   A body the schema rejects is `422 { error: "invalid_request" }`.
 *   [clause:finish-invalid-422]
 * - `/internal/workflows/runs/{runId}/finalize` with a `finalize.input` body →
 *   `200 { ok: true }`; invalid is `422`. [clause:finalize-ok]
 *
 * Idempotency. The key must be `{runId}:{nodeId}:r{runAttempt}:a{nodeAttempt}:
 * {phase}` and agree with the route (its runId, nodeId and phase) and the body
 * (`runAttempt` and, for prepare, `nodeAttempt`); `finalize` takes
 * `{runId}:r{runAttempt}:finalize`. Any other key, or none, is
 * `400 { error: "invalid_idempotency_key" }` and runs nothing.
 * [clause:idempotency-key-format] A key with a stored outcome is answered with
 * that outcome and the call does not run again. [clause:replay-stored-outcome]
 * Two keys that differ only in `r{runAttempt}` are different calls, so a run
 * retry re-executes a node whose `r0` key stored a failure.
 * [clause:run-attempt-distinct-keys] A started call runs to completion even if
 * the client disconnects (its outcome is stored under the key).
 * [clause:call-survives-disconnect]
 *
 * `GET /internal/workflows/invocations/{key}` → `200` with the stored outcome
 * when the call with that key completed [clause:lookup-found], `404 { error:
 * "not_found" }` otherwise, including a call still in flight.
 * [clause:lookup-not-found] Fenced like the other routes.
 *
 * `429` and `503` (optionally with `Retry-After`) and any `5xx` are outages the
 * engine retries with the same key. [clause:retry-after]
 *
 * Test controls: scripted answers per node and phase ({@link FakeWorkflowBackend.script}),
 * per-key execution counts ({@link FakeWorkflowBackend.executions}), stored
 * outcomes, the fence marks, and a log of every request with its headers.
 */
import { readFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { JsonValue } from "../../src/core/json.js";
import {
  ContractError,
  parseFinalizeInput,
  parseFinishInput,
} from "../../src/workflow/contract.js";

const FIXTURES = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "contracts",
  "workflow-nodes",
  "fixtures",
);

/** A committed contract fixture (without the `.json`), e.g. `prepare.wait.duration.valid`. */
export function fixture(name: string): Record<string, JsonValue> {
  return JSON.parse(readFileSync(join(FIXTURES, `${name}.json`), "utf8")) as Record<
    string,
    JsonValue
  >;
}

/** How the fake reacts to one call that is not answered from a stored outcome. */
export type FakeScript =
  | { body: JsonValue }
  | { status: number; body?: JsonValue; headers?: Record<string, string> }
  /** Never answer (until the client gives up). The call does not run. */
  | { hang: true }
  /** Run the call and store its outcome, then never answer. */
  | { hangAfterEffect: JsonValue };

export interface FakeRequest {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: JsonValue | null;
}

export interface FakeWorkflowBackendOptions {
  serviceToken?: string;
}

const NODE_ROUTE = /^\/internal\/workflows\/runs\/([^/]+)\/nodes\/([^/]+)\/(prepare|finish)$/;
const FINALIZE_ROUTE = /^\/internal\/workflows\/runs\/([^/]+)\/finalize$/;
const LOOKUP_ROUTE = /^\/internal\/workflows\/invocations\/(.+)$/;

const reEscape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export class FakeWorkflowBackend {
  readonly serviceToken: string;
  readonly requests: FakeRequest[] = [];
  readonly #server: Server;
  readonly #scripts = new Map<string, FakeScript[]>();
  readonly #outcomes = new Map<string, JsonValue>();
  readonly #executions = new Map<string, number>();
  readonly #highWater = new Map<string, number>();
  /** Answered by prepare when nothing is scripted for the node. */
  defaultPrepare: JsonValue = fixture("prepare.final.valid");
  /** Answered by finish when nothing is scripted for the node. */
  defaultFinish: JsonValue = fixture("prepare.final.valid");
  #baseUrl = "";

  private constructor(options: FakeWorkflowBackendOptions) {
    this.serviceToken = options.serviceToken ?? "service-token";
    this.#server = createServer((req, res) => {
      void this.#handle(req, res).catch((err) => {
        if (!res.headersSent) send(res, 500, { error: String(err) });
        else res.destroy();
      });
    });
  }

  static async start(options: FakeWorkflowBackendOptions = {}): Promise<FakeWorkflowBackend> {
    const backend = new FakeWorkflowBackend(options);
    await new Promise<void>((resolve) => backend.#server.listen(0, "127.0.0.1", resolve));
    backend.#baseUrl = `http://127.0.0.1:${(backend.#server.address() as AddressInfo).port}`;
    return backend;
  }

  get baseUrl(): string {
    return this.#baseUrl;
  }

  /** Queue reactions for the next calls of `nodeId`'s `phase` that are not replayed from a stored key. */
  script(nodeId: string, phase: "prepare" | "finish", ...scripts: FakeScript[]): this {
    const k = `${nodeId}:${phase}`;
    this.#scripts.set(k, [...(this.#scripts.get(k) ?? []), ...scripts]);
    return this;
  }

  /** How many times the call under `key` actually ran. */
  executions(key: string): number {
    return this.#executions.get(key) ?? 0;
  }

  /** Total executions of `nodeId`'s `phase` across keys. */
  total(nodeId: string, phase: "prepare" | "finish"): number {
    let n = 0;
    for (const [key, count] of this.#executions) {
      if (key.includes(`:${nodeId}:`) && key.endsWith(`:${phase}`)) n += count;
    }
    return n;
  }

  /** Idempotency keys of the node calls received, in arrival order. */
  keys(phase?: "prepare" | "finish"): string[] {
    return this.requests
      .filter((r) => r.method === "POST" && NODE_ROUTE.test(r.path))
      .filter((r) => phase === undefined || r.path.endsWith(`/${phase}`))
      .map((r) => r.headers["idempotency-key"] ?? "");
  }

  /** Requests to a node route, in order. */
  calls(phase: "prepare" | "finish" | "finalize"): FakeRequest[] {
    return this.requests.filter((r) => r.method === "POST" && r.path.endsWith(`/${phase}`));
  }

  storeOutcome(key: string, outcome: JsonValue): this {
    this.#outcomes.set(key, outcome);
    return this;
  }

  outcome(key: string): JsonValue | undefined {
    return this.#outcomes.get(key);
  }

  setHighWater(session: string, token: number): this {
    this.#highWater.set(session, Math.max(token, this.#highWater.get(session) ?? 0));
    return this;
  }

  highWater(session: string): number | undefined {
    return this.#highWater.get(session);
  }

  close(): Promise<void> {
    this.#server.closeAllConnections();
    return new Promise((resolve) => this.#server.close(() => resolve()));
  }

  async #handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const path = (req.url ?? "/").split("?")[0] ?? "/";
    const raw = await readBody(req);
    let body: JsonValue | null = null;
    if (raw.length > 0) {
      try {
        body = JSON.parse(raw) as JsonValue;
      } catch {
        this.#record(req, path, null);
        return send(res, 400, { error: "invalid_request", message: "body is not JSON" });
      }
    }
    this.#record(req, path, body);
    if (req.headers.authorization !== `Bearer ${this.serviceToken}`) {
      return send(res, 401, { error: "unauthorized" });
    }
    const session = header(req, "x-engine-session");
    if (session !== undefined && !this.#admitFence(req, session, res)) return;

    const lookup = LOOKUP_ROUTE.exec(path);
    if (req.method === "GET" && lookup) {
      const stored = this.#outcomes.get(decodeURIComponent(lookup[1] as string));
      return stored === undefined ? send(res, 404, { error: "not_found" }) : send(res, 200, stored);
    }
    const node = NODE_ROUTE.exec(path);
    if (req.method === "POST" && node) {
      return this.#node(
        req,
        res,
        body,
        decodeURIComponent(node[1] as string),
        decodeURIComponent(node[2] as string),
        node[3] as "prepare" | "finish",
      );
    }
    const finalize = FINALIZE_ROUTE.exec(path);
    if (req.method === "POST" && finalize) {
      return this.#finalize(req, res, body, decodeURIComponent(finalize[1] as string));
    }
    return send(res, 404, { error: "not_found" });
  }

  async #node(
    req: IncomingMessage,
    res: ServerResponse,
    body: JsonValue | null,
    runId: string,
    nodeId: string,
    phase: "prepare" | "finish",
  ): Promise<void> {
    const key = header(req, "idempotency-key") ?? "";
    const m = new RegExp(`^${reEscape(runId)}:${reEscape(nodeId)}:r(\\d+):a(\\d+):${phase}$`).exec(
      key,
    );
    const obj = body && typeof body === "object" && !Array.isArray(body) ? body : {};
    const keyOk =
      m !== null &&
      (obj.runAttempt === undefined || obj.runAttempt === Number(m[1])) &&
      (phase !== "prepare" || obj.nodeAttempt === undefined || obj.nodeAttempt === Number(m[2]));
    if (!keyOk) {
      return send(res, 400, {
        error: "invalid_idempotency_key",
        message: `expected {runId}:{nodeId}:r{n}:a{n}:${phase}`,
      });
    }
    const stored = this.#outcomes.get(key);
    if (stored !== undefined) return send(res, 200, stored);

    if (phase === "finish") {
      try {
        parseFinishInput(body);
      } catch (err) {
        if (err instanceof ContractError) return send(res, 422, { error: "invalid_request" });
        throw err;
      }
    }
    const scripts = this.#scripts.get(`${nodeId}:${phase}`);
    const script = scripts?.shift();
    const fallback = phase === "prepare" ? this.defaultPrepare : this.defaultFinish;
    if (script && "hang" in script) return;
    if (script && "status" in script) {
      return send(res, script.status, script.body ?? { error: "scripted" }, script.headers);
    }
    const answer =
      script && "hangAfterEffect" in script
        ? script.hangAfterEffect
        : script && "body" in script
          ? script.body
          : fallback;
    this.#executions.set(key, (this.#executions.get(key) ?? 0) + 1);
    this.#outcomes.set(key, answer);
    if (script && "hangAfterEffect" in script) return;
    return send(res, 200, answer);
  }

  #finalize(
    req: IncomingMessage,
    res: ServerResponse,
    body: JsonValue | null,
    runId: string,
  ): void {
    this.#finalizeCall(req, res, body, runId);
  }

  #finalizeCall(
    req: IncomingMessage,
    res: ServerResponse,
    body: JsonValue | null,
    runId: string,
  ): void {
    const key = header(req, "idempotency-key") ?? "";
    const m = new RegExp(`^${reEscape(runId)}:r(\\d+):finalize$`).exec(key);
    if (!m) {
      send(res, 400, { error: "invalid_idempotency_key" });
      return;
    }
    try {
      parseFinalizeInput(body);
    } catch (err) {
      if (!(err instanceof ContractError)) throw err;
      send(res, 422, { error: "invalid_request" });
      return;
    }
    if (!this.#outcomes.has(key)) {
      this.#executions.set(key, (this.#executions.get(key) ?? 0) + 1);
      this.#outcomes.set(key, { ok: true });
    }
    send(res, 200, { ok: true });
  }

  #admitFence(req: IncomingMessage, session: string, res: ServerResponse): boolean {
    const fenceHeader = header(req, "x-engine-fence");
    if (fenceHeader === undefined) return true;
    const token = Number(fenceHeader);
    if (!Number.isSafeInteger(token)) {
      send(res, 400, { error: "invalid_fence" });
      return false;
    }
    const high = this.#highWater.get(session) ?? 0;
    if (token < high) {
      send(res, 409, { error: "fence_rejected", highWater: high });
      return false;
    }
    this.#highWater.set(session, token);
    return true;
  }

  #record(req: IncomingMessage, path: string, body: JsonValue | null): void {
    const headers: Record<string, string> = {};
    for (const [k, v] of Object.entries(req.headers)) {
      if (v !== undefined) headers[k] = Array.isArray(v) ? v.join(", ") : v;
    }
    this.requests.push({ method: req.method ?? "GET", path, headers, body });
  }
}

function header(req: IncomingMessage, name: string): string | undefined {
  const v = req.headers[name];
  return Array.isArray(v) ? v[0] : v;
}

function send(
  res: ServerResponse,
  status: number,
  body: JsonValue,
  headers: Record<string, string> = {},
): void {
  if (res.destroyed) return;
  const text = JSON.stringify(body);
  res.writeHead(status, {
    ...headers,
    "content-type": "application/json",
    "content-length": Buffer.byteLength(text),
  });
  res.end(text);
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}
