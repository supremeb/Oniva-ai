/**
 * A fake of Oniva's tool gateway, and the specification of its contract (R18).
 *
 * The engine talks to the gateway over HTTP with three routes. Every request
 * carries `Authorization: Bearer <service token>`; a wrong or missing token is
 * answered `401 { error: "unauthorized" }`.
 *
 * Fencing. Any request may carry `X-Engine-Fence: <token>` with
 * `X-Engine-Session: <session id>` (the engine sends both on every request
 * while it holds a lease: manifest loads, invokes and reconciles). The
 * gateway keeps a per-session high-water mark: a token lower than the mark is
 * answered `409 { error: "fence_rejected", highWater }` and has no effect; any
 * other token raises the mark. Raising it on reads too matters: a successor
 * that only reconciles (and so never invokes) must still lock out the
 * previous owner's invokes that are delayed on the wire. An invoke carrying no
 * `X-Engine-Session` is keyed by `tenantId/projectId`; on the other routes a
 * fence without a session is not checked. The gateway checks an invoke's
 * token again, atomically, when the effect commits, so an invoke accepted
 * just before a successor raised the mark still causes no effect (and stores
 * no outcome); that is what makes a successor's `404` from the reconcile
 * route final.
 *
 * `GET /internal/tools/manifest` → `200 { tools: ManifestTool[] }`, where a
 * tool is `{ name, description, parameters, sideEffect, idempotent?,
 * timeoutMs, requiresApproval? }`: `parameters` is a JSON Schema for the
 * argument object, `sideEffect` is `"none"` (a read) or `"write"`, and
 * `idempotent` (default false) says a write may safely run again under the
 * same idempotency key.
 *
 * `POST /internal/tools/{name}/invoke` with body
 * `{ args, runId, toolCallId, tenantId, projectId }` and headers
 * `Idempotency-Key: runId:toolCallId` and, when the engine holds a lease,
 * `X-Engine-Fence: <token>` and `X-Engine-Session: <session id>`:
 * - `200 Outcome` where `Outcome = { content: string | {type:"text",text}[],
 *   isError?: boolean, details?: JSON }`;
 * - a key that already has a completed outcome is answered with that stored
 *   outcome and the tool does not run again;
 * - `409 { error: "fence_rejected", highWater }` (see Fencing);
 * - `404 { error: "unknown_tool" }`, `400 { error: "invalid_request" }`, `429`
 *   or `503` (optionally with `Retry-After`), and any `5xx` for gateway
 *   failures.
 * A started effect runs to completion even if the client disconnects (the
 * client timed out, or its process died); its outcome is stored under the
 * key, which is how the engine learns what a timed-out write did.
 *
 * `GET /internal/tools/invocations/{key}` → `200 Outcome` when the call with
 * that idempotency key completed, `404 { error: "not_found" }` otherwise
 * (including a call still in flight). This is what reconciliation after a
 * restart, or after a write timed out, reads. Fenced like the other routes.
 *
 * Test controls: per-tool effect counters ({@link FakeGateway.effects}),
 * scripted failures and delays, stored outcomes by key, the per-session fence
 * high-water mark, and a log of every request with its headers.
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import type { JsonObject, JsonValue } from "../../src/core/json.js";

export interface FakeOutcome {
  content: string | { type: "text"; text: string }[];
  isError?: boolean;
  details?: JsonValue;
}

export interface FakeInvocation {
  args: JsonObject;
  runId: string;
  toolCallId: string;
  tenantId: string;
  projectId: string;
  idempotencyKey: string;
}

export interface FakeToolSpec {
  name: string;
  description: string;
  parameters: JsonObject;
  sideEffect: "none" | "write";
  idempotent?: boolean;
  timeoutMs: number;
  requiresApproval?: boolean;
  /** Runs the effect. Default: echo the arguments. */
  handler?: (call: FakeInvocation) => FakeOutcome | Promise<FakeOutcome>;
}

/**
 * One scripted reaction to the next invoke of a tool, consumed in order:
 * - `{ status, body?, headers? }`: answer with that status without running the
 *   tool;
 * - `{ hang: true }`: never answer and never run (until the client gives up);
 * - `{ dropAfterEffect: true }`: run the tool and store its outcome, then
 *   destroy the connection without answering (a response lost in transit);
 * - `{ hangAfterEffect: true }`: run the tool and store its outcome, then never
 *   answer (until the client gives up).
 */
export type FakeScript =
  | { status: number; body?: JsonValue; headers?: Record<string, string> }
  | { hang: true }
  | { dropAfterEffect: true }
  | { hangAfterEffect: true };

export interface FakeRequest {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: JsonValue | null;
}

export interface FakeGatewayOptions {
  serviceToken?: string;
  /** Reject invokes that carry no `X-Engine-Fence` header (400). Default false. */
  requireFence?: boolean;
}

const MANIFEST = "/internal/tools/manifest";
const INVOKE = /^\/internal\/tools\/([^/]+)\/invoke$/;
const INVOCATION = /^\/internal\/tools\/invocations\/([^/]+)$/;

export class FakeGateway {
  readonly serviceToken: string;
  readonly requests: FakeRequest[] = [];
  /** Manifest served; edit freely between calls. */
  readonly tools: FakeToolSpec[];
  readonly #server: Server;
  readonly #effects = new Map<string, number>();
  readonly #scripts = new Map<string, FakeScript[]>();
  readonly #delays = new Map<string, number>();
  readonly #outcomes = new Map<string, FakeOutcome>();
  readonly #highWater = new Map<string, number>();
  readonly #requireFence: boolean;
  #manifestFailures: number[] = [];
  #baseUrl = "";

  private constructor(tools: FakeToolSpec[], options: FakeGatewayOptions) {
    this.tools = tools;
    this.serviceToken = options.serviceToken ?? "service-token";
    this.#requireFence = options.requireFence ?? false;
    this.#server = createServer((req, res) => {
      void this.#handle(req, res).catch((err) => {
        if (!res.headersSent) send(res, 500, { error: String(err) });
        else res.destroy();
      });
    });
  }

  static async start(
    tools: FakeToolSpec[],
    options: FakeGatewayOptions = {},
  ): Promise<FakeGateway> {
    const gw = new FakeGateway(tools, options);
    await new Promise<void>((resolve) => gw.#server.listen(0, "127.0.0.1", resolve));
    const { port } = gw.#server.address() as AddressInfo;
    gw.#baseUrl = `http://127.0.0.1:${port}`;
    return gw;
  }

  get baseUrl(): string {
    return this.#baseUrl;
  }

  /** How many times the tool's effect ran. */
  effects(name: string): number {
    return this.#effects.get(name) ?? 0;
  }

  /** Requests to invoke `name`, in arrival order. */
  invokes(name: string): FakeRequest[] {
    return this.requests.filter(
      (r) => r.method === "POST" && r.path === `/internal/tools/${name}/invoke`,
    );
  }

  /** Queue reactions for the next invokes of `name`. */
  script(name: string, ...scripts: FakeScript[]): this {
    this.#scripts.set(name, [...(this.#scripts.get(name) ?? []), ...scripts]);
    return this;
  }

  /** Fail the next manifest loads with these statuses. */
  failManifest(...statuses: number[]): this {
    this.#manifestFailures.push(...statuses);
    return this;
  }

  /**
   * Wait this long before running `name` (the request is answered after).
   * The effect runs even if the client disconnects meanwhile.
   */
  delay(name: string, ms: number): this {
    this.#delays.set(name, ms);
    return this;
  }

  /** Record a completed outcome for an idempotency key. */
  storeOutcome(key: string, outcome: FakeOutcome): this {
    this.#outcomes.set(key, outcome);
    return this;
  }

  outcome(key: string): FakeOutcome | undefined {
    return this.#outcomes.get(key);
  }

  /** Raise (never lower) the fence high-water mark of a session key. */
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
    const invoke = INVOKE.exec(path);
    if (!(req.method === "POST" && invoke)) {
      const session = header(req, "x-engine-session");
      if (session !== undefined && !this.#admitFence(req, session, res)) return;
    }
    if (req.method === "GET" && path === MANIFEST) {
      const failure = this.#manifestFailures.shift();
      if (failure !== undefined) return send(res, failure, { error: "scripted" });
      return send(res, 200, {
        tools: this.tools.map(({ handler: _h, ...spec }) => spec) as unknown as JsonValue,
      });
    }
    const invocation = INVOCATION.exec(path);
    if (req.method === "GET" && invocation) {
      const stored = this.#outcomes.get(decodeURIComponent(invocation[1] as string));
      return stored
        ? send(res, 200, stored as unknown as JsonValue)
        : send(res, 404, { error: "not_found" });
    }
    if (req.method === "POST" && invoke) {
      return this.#invoke(decodeURIComponent(invoke[1] as string), req, res, body);
    }
    return send(res, 404, { error: "not_found" });
  }

  async #invoke(
    name: string,
    req: IncomingMessage,
    res: ServerResponse,
    body: JsonValue | null,
  ): Promise<void> {
    const tool = this.tools.find((t) => t.name === name);
    if (!tool) return send(res, 404, { error: "unknown_tool" });
    const key = header(req, "idempotency-key");
    const b = body as Partial<FakeInvocation> | null;
    if (
      !b ||
      typeof b !== "object" ||
      typeof b.args !== "object" ||
      b.args === null ||
      Array.isArray(b.args) ||
      typeof b.runId !== "string" ||
      typeof b.toolCallId !== "string" ||
      typeof b.tenantId !== "string" ||
      typeof b.projectId !== "string" ||
      key !== `${b.runId}:${b.toolCallId}`
    ) {
      return send(res, 400, { error: "invalid_request" });
    }
    const call: FakeInvocation = { ...(b as FakeInvocation), idempotencyKey: key };

    if (header(req, "x-engine-fence") === undefined && this.#requireFence) {
      return send(res, 400, { error: "fence_required" });
    }
    const session = header(req, "x-engine-session") ?? `${call.tenantId}/${call.projectId}`;
    if (!this.#admitFence(req, session, res)) return;

    const stored = this.#outcomes.get(key);
    if (stored) return send(res, 200, stored as unknown as JsonValue);

    const script = this.#scripts.get(name)?.shift();
    if (script && "status" in script) {
      return send(res, script.status, script.body ?? { error: "scripted" }, script.headers);
    }
    if (script && "hang" in script) return; // never answered; the client aborts

    // A disconnect does not cancel the work: real backends finish it.
    const delay = this.#delays.get(name) ?? 0;
    if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));

    // Commit-time fence check: a successor may have raised the mark meanwhile.
    if (!this.#admitFence(req, session, res)) return;
    this.#effects.set(name, this.effects(name) + 1);
    const outcome = tool.handler
      ? await tool.handler(call)
      : { content: JSON.stringify(call.args), details: call.args };
    this.#outcomes.set(key, outcome);
    if (script && "dropAfterEffect" in script) {
      res.destroy();
      return;
    }
    if (script && "hangAfterEffect" in script) return;
    send(res, 200, outcome as unknown as JsonValue);
  }

  /**
   * Check a request's fence token against the session's high-water mark and
   * raise the mark; answers 400/409 and returns false when it is refused. A
   * request without a token passes.
   */
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
