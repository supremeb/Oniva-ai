/**
 * A fake of Oniva's tool gateway, and the specification of its contract (R18).
 *
 * Each `[clause:<id>]` tag marks a behavior the Python backend must reproduce;
 * `backend/tests/internal/test_contract_conformance.py` reads these tags and
 * fails when one has no conformance test, so adding a clause here without a
 * matching Python test breaks the backend suite.
 *
 * The engine talks to the gateway over HTTP with three routes. Every request
 * carries `Authorization: Bearer <service token>`; a wrong or missing token is
 * answered `401 { error: "unauthorized" }`. [clause:auth-401]
 *
 * Fencing. Any request may carry `X-Engine-Fence: <token>` with
 * `X-Engine-Session: <session id>` (the engine sends both on every request
 * while it holds a lease: manifest loads, invokes and reconciles). The
 * gateway keeps a per-session high-water mark: a token lower than the mark is
 * answered `409 { error: "fence_rejected", highWater }` and has no effect
 * [clause:fence-409]; any other token raises the mark. Raising it on reads
 * too matters: a successor that only reconciles (and so never invokes) must
 * still lock out the previous owner's invokes that are delayed on the wire
 * [clause:fence-raise-on-read]. A token that is not an integer is answered
 * `400 { error: "invalid_fence" }` [clause:fence-invalid-400]. An invoke
 * carrying no `X-Engine-Session` is keyed by `tenantId/projectId`
 * [clause:fence-invoke-session-default]; on the other routes a fence without
 * a session is not checked [clause:fence-sessionless-unchecked]. The gateway
 * checks an invoke's token again, atomically, when the effect commits, so an
 * invoke accepted just before a successor raised the mark still causes no
 * effect (and stores no outcome); that is what makes a successor's `404` from
 * the reconcile route final. [clause:fence-commit-recheck]
 *
 * `GET /internal/tools/manifest` → `200 { tools: ManifestTool[] }`, where a
 * tool is `{ name, description, parameters, sideEffect, idempotent?,
 * timeoutMs, requiresApproval? }`: `parameters` is a JSON Schema for the
 * argument object, `sideEffect` is `"none"` (a read) or `"write"`, and
 * `idempotent` (default false) says a write may safely run again under the
 * same idempotency key. [clause:manifest-shape]
 *
 * `POST /internal/tools/{name}/invoke` with body
 * `{ args, runId, toolCallId, tenantId, projectId }` and headers
 * `Idempotency-Key: runId:toolCallId` and, when the engine holds a lease,
 * `X-Engine-Fence: <token>` and `X-Engine-Session: <session id>`:
 * - `200 Outcome` where `Outcome = { content: string | {type:"text",text}[],
 *   isError?: boolean, details?: JSON }`; [clause:invoke-outcome-shape]
 * - a key that already has a completed outcome is answered with that stored
 *   outcome and the tool does not run again; [clause:invoke-replay-stored]
 * - `409 { error: "fence_rejected", highWater }` (see Fencing);
 * - `404 { error: "unknown_tool" }` [clause:invoke-unknown-tool-404], `400 {
 *   error: "invalid_request" }` for a malformed body or a key that is not
 *   `runId:toolCallId` [clause:invoke-invalid-400], `429` or `503` (optionally
 *   with `Retry-After`) [clause:invoke-retry-after], and any `5xx` for gateway
 *   failures.
 * A started effect runs to completion even if the client disconnects (the
 * client timed out, or its process died); its outcome is stored under the
 * key, which is how the engine learns what a timed-out write did.
 * [clause:invoke-survives-disconnect] An outcome is never larger than the
 * engine's 1 MiB response cap. [clause:outcome-size-cap]
 *
 * `GET /internal/tools/invocations/{key}` → `200 Outcome` when the call with
 * that idempotency key completed [clause:reconcile-found], `404 { error:
 * "not_found" }` otherwise (including a call still in flight)
 * [clause:reconcile-not-found]. This is what reconciliation after a restart,
 * or after a write timed out, reads. Fenced like the other routes.
 * [clause:reconcile-fenced]
 *
 * An invoke body also carries `conversationId` (the engine conversation the
 * call belongs to); the fake accepts it and records it, and an older client
 * without it still works. [clause:invoke-conversation-id] For a subagent's
 * child conversation the engine sends the top-level conversation's id as
 * `conversationId` and the child's own id as `engineConversationId`
 * (informational; the backend ignores it). [clause:engine-conversation-id]
 *
 * Model policy and usage (the backend's `/internal/models/*`; the real
 * contract lives in `backend/app/internal_api/models.py`). Same bearer token;
 * neither route is fenced. [clause:models-unfenced]
 *
 * `POST /internal/models/policy` with `{ tenantId, projectId?, conversationId?,
 * runId, round, purpose, requestId, agent: { model?, tier? },
 * supportedProviders }` → always `200` with `Cache-Control: no-store`
 * [clause:policy-no-store]: `{ ok: true, provider, model, apiKey, maxTokens,
 * expiresAt (epoch ms), requestId }` [clause:policy-allow-shape], or a denial
 * `{ ok: false, code, reason, retryable }` [clause:policy-denial-shape]. A
 * body without `requestId` or `tenantId` is `422 { error: "invalid_request"
 * }`. [clause:policy-invalid-422]
 *
 * `POST /internal/models/usage` with `{ tenantId, projectId?, conversationId?,
 * runId, requestId, provider, model, usage: { inputTokens, outputTokens,
 * cacheReadTokens, cacheWriteTokens }, latencyMs?, status }` →
 * `200 { recorded }` [clause:usage-recorded]; a repeated `(tenantId,
 * requestId)` is `recorded: false` and counts once [clause:usage-repeat-false],
 * which is what makes the engine's durable outbox safe: a report re-sent after
 * a restart is the same `requestId` and is counted once
 * [clause:usage-outbox-redelivery].
 * A body missing a required field is `422`. [clause:usage-invalid-422]
 * An optional `agentRole` (the subagent tool name, `[a-z0-9_]{1,64}`) is
 * present on usage from a subagent's child conversation and absent for the
 * root conversation [clause:usage-agent-role]; a malformed one is `422`
 * [clause:usage-agent-role-422]. `conversationId` is always the root's.
 *
 * Transient failures of both routes (restart, overload): a dropped
 * connection, a timeout, `408`, `429` or any `5xx` is an outage, not an answer,
 * and the engine retries it with the same `requestId` and body for a bounded
 * time (`ENGINE_POLICY_RETRY_MS`, default 60 s) (engine client: models-retry-transient);
 * `Retry-After` on `429`/`503` sets the next delay (engine client: models-retry-after).
 * The policy route caches the decision per `(tenantId, requestId)`, so a retry
 * after a lost answer returns the same decision [clause:policy-retry-idempotent];
 * the usage route counts a retried report once (see usage-repeat-false above).
 * A definitive answer is never retried: a `200 { ok: false }` denial, `401`,
 * `403`, `422` and any other `4xx` besides `408`/`429` [clause:models-no-retry-definitive].
 * Once the budget is spent a policy request fails the run with a retryable
 * `network` error code `policy_unavailable` (engine client: policy-exhausted-retryable);
 * a run abort stops retrying at once (engine client: models-retry-abort);
 * a spent usage report logs `usage_failed` and is dropped
 * (engine client: usage-exhausted-dropped).
 *
 * Tool authorization (the backend's `/internal/tools/authorize`; the real
 * contract lives in `backend/app/internal_api/authorize.py`). Same bearer
 * token; not fenced. `POST /internal/tools/authorize` with `{ requestId,
 * tenantId, projectId, conversationId, engineConversationId?, agentRole?,
 * runId, toolCallId, toolTaskId, toolName, toolSource: "gateway" | "native",
 * arguments, argumentsHash, sideEffect?, replay, taint: { tainted, sources } }`
 * asks whether one tool call may run. The answer is `200` with
 * `Cache-Control: no-store` [clause:authorize-no-store]:
 * - `{ decision: "allow" }` [clause:authorize-allow-shape];
 * - `{ decision: "block", code, reason }` [clause:authorize-block-shape];
 * - `{ decision: "requireApproval", reason, approvalId, approvalTtlMs,
 *   requiredRole }`, where `approvalTtlMs` is the backend's approval lifetime
 *   in ms or `null` for none (the engine turns it into an expiry on its own
 *   clock) [clause:authorize-require-approval-shape].
 * The same `(tenantId, requestId)` posted again is answered with the same
 * decision and creates no second approval [clause:authorize-repeat-same]. A
 * body missing `requestId`, `tenantId`, `runId`, `toolCallId`, `toolName` or
 * a valid `toolSource`, or whose `arguments` is not an object, is `422 {
 * error: "invalid_request" }` [clause:authorize-invalid-422]. The engine
 * never sends credentials in `arguments` (they are redacted) while
 * `argumentsHash` is the hash of the unredacted arguments (see
 * `test/support/args-hash-vectors.json`).
 * Outages are retried like the model routes, with `requestId`
 * `runId:toolCallId` on every attempt (engine client: authorize-retry-transient);
 * a 401/403, any other 4xx or a malformed body blocks the call at once
 * (engine client: authorize-no-retry-definitive); once the retry budget is spent
 * the call is blocked with reason `policy_unavailable`, never run
 * (engine client: authorize-fail-closed).
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
  /** Absent from clients older than the conversation binding. */
  conversationId?: string;
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
const POLICY = "/internal/models/policy";
const USAGE = "/internal/models/usage";
const AUTHORIZE = "/internal/tools/authorize";
const INVOCATION = /^\/internal\/tools\/invocations\/([^/]+)$/;

/** What the fake answers a model policy request with. */
export type FakePolicy =
  | { ok: true; provider?: string; model?: string; apiKey?: string; maxTokens?: number }
  | { ok: false; code: string; reason: string; retryable?: boolean }
  | { status: number; body?: JsonValue; headers?: Record<string, string> }
  | { hang: true }
  /** Drop the connection without answering (a restarting backend). */
  | { reset: true };

/** What the fake answers a tool authorization request with. */
export type FakeAuthorize =
  | { decision: "allow" }
  | { decision: "block"; code?: string; reason: string }
  | {
      decision: "requireApproval";
      reason?: string;
      approvalTtlMs: number | null;
      requiredRole?: string;
    }
  | { status: number; body?: JsonValue; headers?: Record<string, string> }
  | { hang: true }
  | { reset: true };

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
  readonly #policyScripts: FakePolicy[] = [];
  #usageFailures: number[] = [];
  readonly #authorizeScripts: FakeAuthorize[] = [];
  readonly #authorizeDecided = new Map<string, JsonValue>();
  #approvals = 0;
  /** The answer when no script is queued. Default: allow. */
  defaultAuthorize: FakeAuthorize = { decision: "allow" };
  /** Usage rows recorded, once per `(tenantId, requestId)`. */
  readonly usage: JsonObject[] = [];
  /** The answer when no script is queued. Default: allow with the key `fake-key`. */
  defaultPolicy: FakePolicy = { ok: true };
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

  /** Queue answers for the next policy requests (then `defaultPolicy`). */
  scriptPolicy(...answers: FakePolicy[]): this {
    this.#policyScripts.push(...answers);
    return this;
  }

  /** Queue answers for the next authorize requests (then `defaultAuthorize`). */
  scriptAuthorize(...answers: FakeAuthorize[]): this {
    this.#authorizeScripts.push(...answers);
    return this;
  }

  /** Authorize requests received (including repeats), in order. */
  authorizeRequests(): FakeRequest[] {
    return this.requests.filter((r) => r.method === "POST" && r.path === AUTHORIZE);
  }

  /** Fail the next usage posts with these statuses (nothing is recorded). */
  failUsage(...statuses: number[]): this {
    this.#usageFailures.push(...statuses);
    return this;
  }

  /** Policy requests received, in order. */
  policyRequests(): FakeRequest[] {
    return this.requests.filter((r) => r.method === "POST" && r.path === POLICY);
  }

  /** Usage posts received (including failed and repeated ones), in order. */
  usageRequests(): FakeRequest[] {
    return this.requests.filter((r) => r.method === "POST" && r.path === USAGE);
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
    if (req.method === "POST" && path === POLICY) return this.#policy(res, body);
    if (req.method === "POST" && path === USAGE) return this.#recordUsage(res, body);
    if (req.method === "POST" && path === AUTHORIZE) return this.#authorize(res, body);
    const invoke = INVOKE.exec(path);
    if (!(req.method === "POST" && invoke)) {
      const session = header(req, "x-engine-session");
      if (session !== undefined && !this.#admitFence(req, session, res)) return;
    }
    if (req.method === "GET" && path === MANIFEST) {
      const failure = this.#manifestFailures.shift();
      if (failure !== undefined) {
        send(res, failure, { error: "scripted" });
        return;
      }
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

  async #policy(res: ServerResponse, body: JsonValue | null): Promise<void> {
    const b = body && typeof body === "object" && !Array.isArray(body) ? body : null;
    if (!b || typeof b.requestId !== "string" || typeof b.tenantId !== "string") {
      send(res, 422, { error: "invalid_request" });
      return;
    }
    const answer = this.#policyScripts.shift() ?? this.defaultPolicy;
    if ("hang" in answer) return;
    if ("reset" in answer) {
      res.socket?.destroy();
      return;
    }
    if ("status" in answer) {
      return send(res, answer.status, answer.body ?? { error: "scripted" }, answer.headers);
    }
    const headers = { "cache-control": "no-store" };
    if (!answer.ok) {
      return send(res, 200, { retryable: false, ...answer } as unknown as JsonValue, headers);
    }
    return send(
      res,
      200,
      {
        ok: true,
        provider: answer.provider ?? "fake",
        model: answer.model ?? "fake-model",
        apiKey: answer.apiKey ?? "fake-key",
        maxTokens: answer.maxTokens ?? 1024,
        expiresAt: Date.now() + 60_000,
        requestId: b.requestId,
      },
      headers,
    );
  }

  #authorize(res: ServerResponse, body: JsonValue | null): void {
    const b = body && typeof body === "object" && !Array.isArray(body) ? body : null;
    const text = (v: JsonValue | undefined): v is string => typeof v === "string" && v !== "";
    if (
      !b ||
      !text(b.requestId) ||
      !text(b.tenantId) ||
      !text(b.runId) ||
      !text(b.toolCallId) ||
      !text(b.toolName) ||
      (b.toolSource !== "gateway" && b.toolSource !== "native") ||
      typeof b.arguments !== "object" ||
      b.arguments === null ||
      Array.isArray(b.arguments)
    ) {
      send(res, 422, { error: "invalid_request" });
      return;
    }
    const key = `${b.tenantId}/${b.requestId}`;
    const headers = { "cache-control": "no-store" };
    const decided = this.#authorizeDecided.get(key);
    if (decided) {
      send(res, 200, decided, headers);
      return;
    }
    const answer = this.#authorizeScripts.shift() ?? this.defaultAuthorize;
    if ("hang" in answer) return;
    if ("reset" in answer) {
      res.socket?.destroy();
      return;
    }
    if ("status" in answer) {
      send(res, answer.status, answer.body ?? { error: "scripted" }, answer.headers);
      return;
    }
    let out: JsonValue;
    if (answer.decision === "allow") out = { decision: "allow" };
    else if (answer.decision === "block") {
      out = { decision: "block", code: answer.code ?? "blocked", reason: answer.reason };
    } else {
      this.#approvals += 1;
      out = {
        decision: "requireApproval",
        reason: answer.reason ?? `${b.toolName} requires approval`,
        approvalId: `appr-fake-${this.#approvals}`,
        approvalTtlMs: answer.approvalTtlMs,
        requiredRole: answer.requiredRole ?? "admin",
      };
    }
    this.#authorizeDecided.set(key, out);
    send(res, 200, out, headers);
  }

  #recordUsage(res: ServerResponse, body: JsonValue | null): void {
    const failure = this.#usageFailures.shift();
    if (failure !== undefined) {
      send(res, failure, { error: "scripted" });
      return;
    }
    const b = body && typeof body === "object" && !Array.isArray(body) ? body : null;
    if (
      !b ||
      typeof b.requestId !== "string" ||
      typeof b.tenantId !== "string" ||
      typeof b.runId !== "string" ||
      typeof b.provider !== "string" ||
      typeof b.model !== "string"
    ) {
      send(res, 422, { error: "invalid_request" });
      return;
    }
    if (
      b.agentRole !== undefined &&
      (typeof b.agentRole !== "string" || !/^[a-z0-9_]{1,64}$/.test(b.agentRole))
    ) {
      send(res, 422, { error: "invalid_request" });
      return;
    }
    const seen = this.usage.some((u) => u.tenantId === b.tenantId && u.requestId === b.requestId);
    if (!seen) this.usage.push(b);
    send(res, 200, { recorded: !seen }, { "cache-control": "no-store" });
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
