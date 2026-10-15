/**
 * Scenario-suite support: worker processes, a gated fake gateway, a scripted
 * model file and committed-state inspection over Postgres.
 *
 * Workers run `scripts/worker-test-harness.ts` (or the production
 * `scripts/worker.ts`) as child processes and log JSON lines on stdout; tests
 * wait on those events instead of sleeping. Gateway tools block on a
 * {@link Gate} at marked points so a test can kill a worker mid-call.
 */
import { type ChildProcess, spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, afterEach, beforeAll } from "vitest";
import type { JsonObject } from "../../src/core/json.js";
import { PostgresStorageProvider } from "../../src/storage/postgres/storage.js";
import type { SessionStorage, Stored, SubmissionRecord } from "../../src/storage/types.js";
import { AUTH, call, type JsonResponse, TOKEN } from "../server/support.js";
import {
  FakeGateway,
  type FakeInvocation,
  type FakeOutcome,
  type FakeToolSpec,
} from "../support/fake-gateway.js";
import { type PgTestEnv, usePostgres } from "../support/pg.js";
import type { ModelScript } from "../support/scripted-model.js";

export { AUTH, call, TOKEN };

export const ENGINE_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const LEASE_TTL_MS = 2_000;
export const PRINCIPAL = { "x-oniva-principal": "user:alice" };

// ---------------------------------------------------------------------------
// Waiting
// ---------------------------------------------------------------------------

/** Poll `probe` until it returns a non-null, non-false value. */
export async function until<T>(
  probe: () => T | null | undefined | false | Promise<T | null | undefined | false>,
  what: string,
  timeoutMs = 20_000,
  intervalMs = 25,
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await probe();
    if (value !== null && value !== undefined && value !== false) return value as T;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// ---------------------------------------------------------------------------
// Worker processes
// ---------------------------------------------------------------------------

export interface WorkerEvent {
  t: number;
  event: string;
  /** Test-side arrival time. */
  at: number;
  [key: string]: unknown;
}

export class WorkerProc {
  readonly child: ChildProcess;
  readonly events: WorkerEvent[] = [];
  readonly exited: Promise<{ code: number | null; signal: NodeJS.Signals | null }>;
  stderr = "";
  #stdout = "";
  #listeners = new Set<() => void>();

  constructor(script: string, env: Record<string, string>) {
    this.child = spawn(process.execPath, ["--import", "tsx", script], {
      cwd: ENGINE_DIR,
      env: { ...process.env, ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child.stdout?.setEncoding("utf8").on("data", (chunk: string) => {
      this.#stdout += chunk;
      for (let i = this.#stdout.indexOf("\n"); i >= 0; i = this.#stdout.indexOf("\n")) {
        const line = this.#stdout.slice(0, i);
        this.#stdout = this.#stdout.slice(i + 1);
        try {
          this.events.push({ ...(JSON.parse(line) as WorkerEvent), at: Date.now() });
        } catch {
          this.stderr += `[stdout] ${line}\n`;
        }
      }
      for (const l of this.#listeners) l();
    });
    this.child.stderr?.setEncoding("utf8").on("data", (chunk: string) => {
      this.stderr += chunk;
    });
    this.exited = new Promise((resolve) => {
      this.child.once("exit", (code, signal) => {
        for (const l of this.#listeners) l();
        resolve({ code, signal });
      });
    });
  }

  get alive(): boolean {
    return this.child.exitCode === null && this.child.signalCode === null;
  }

  /** The first logged event of this name (and predicate), waiting for it. */
  waitFor(
    event: string,
    pred: (e: WorkerEvent) => boolean = () => true,
    timeoutMs = 30_000,
  ): Promise<WorkerEvent> {
    const find = () => this.events.find((e) => e.event === event && pred(e));
    const found = find();
    if (found) return Promise.resolve(found);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#listeners.delete(check);
        reject(new Error(`worker never logged ${event}; stderr:\n${this.stderr.slice(-4000)}`));
      }, timeoutMs);
      const check = () => {
        const e = find();
        if (e) {
          clearTimeout(timer);
          this.#listeners.delete(check);
          resolve(e);
        } else if (!this.alive) {
          clearTimeout(timer);
          this.#listeners.delete(check);
          reject(
            new Error(
              `worker exited before logging ${event}; stderr:\n${this.stderr.slice(-4000)}`,
            ),
          );
        }
      };
      this.#listeners.add(check);
    });
  }

  async url(): Promise<string> {
    return (await this.waitFor("listening")).url as string;
  }

  /** Resolves with the worker's URL once it serves (holds the lease). */
  async serving(timeoutMs = 30_000): Promise<string> {
    await this.waitFor("serving", () => true, timeoutMs);
    return this.url();
  }

  get ownerId(): string | undefined {
    return this.events.find((e) => e.event === "listening")?.ownerId as string | undefined;
  }

  kill(signal: NodeJS.Signals = "SIGKILL"): void {
    if (this.alive) this.child.kill(signal);
  }
}

// ---------------------------------------------------------------------------
// Gated fake gateway
// ---------------------------------------------------------------------------

/** Holds one invoke at the gateway until released. */
export class Gate {
  readonly entered: Promise<FakeInvocation>;
  readonly #enter: (call: FakeInvocation) => void;
  #settle: ((result: FakeOutcome | Error) => void) | null = null;
  #pending: FakeOutcome | Error | null = null;

  constructor() {
    let enter!: (call: FakeInvocation) => void;
    this.entered = new Promise((resolve) => {
      enter = resolve;
    });
    this.#enter = enter;
  }

  /** Called by the gateway handler: signal entry, then wait for release. */
  hold(call: FakeInvocation): Promise<FakeOutcome> {
    this.#enter(call);
    return new Promise((resolve, reject) => {
      const settle = (r: FakeOutcome | Error) => (r instanceof Error ? reject(r) : resolve(r));
      if (this.#pending) settle(this.#pending);
      else this.#settle = settle;
    });
  }

  /** Let the effect finish with `outcome` (stored under the call's key). */
  release(outcome: FakeOutcome): void {
    this.#finish(outcome);
  }

  /** Make the effect fail: the gateway answers 500 and stores no outcome. */
  fail(message = "effect failed after the caller vanished"): void {
    this.#finish(new Error(message));
  }

  #finish(result: FakeOutcome | Error): void {
    if (this.#settle) this.#settle(result);
    else this.#pending = result;
  }
}

const ANY_ARGS: JsonObject = {
  type: "object",
  properties: { note: { type: "string" } },
};

function tool(name: string, extra: Partial<FakeToolSpec>): FakeToolSpec {
  return {
    name,
    description: `${name} (scenario tool)`,
    parameters: ANY_ARGS,
    sideEffect: "write",
    timeoutMs: 60_000,
    ...extra,
  };
}

/** The fake gateway with gates per tool. */
export class ScenarioGateway {
  readonly gw: FakeGateway;
  readonly #gates = new Map<string, Gate[]>();
  readonly #open = new Set<Gate>();

  private constructor(gw: FakeGateway) {
    this.gw = gw;
  }

  static async start(): Promise<ScenarioGateway> {
    let self: ScenarioGateway | null = null;
    const handler =
      (name: string) =>
      (call: FakeInvocation): Promise<FakeOutcome> | FakeOutcome => {
        const gate = self ? self.#gates.get(name)?.shift() : undefined;
        if (gate) return gate.hold(call);
        return { content: `${name} ok`, details: call.args };
      };
    const specs: FakeToolSpec[] = [
      tool("write_once", { sideEffect: "write" }),
      tool("write_idem", { sideEffect: "write", idempotent: true }),
      tool("slow_read", { sideEffect: "none" }),
      tool("approve_write", { sideEffect: "write", requiresApproval: true }),
      tool("ping", { sideEffect: "none" }),
    ].map((s) => ({ ...s, handler: handler(s.name) }));
    const gw = await FakeGateway.start(specs, { serviceToken: TOKEN });
    self = new ScenarioGateway(gw);
    return self;
  }

  /** Hold the next invoke of `tool` that reaches the effect. */
  gate(tool: string): Gate {
    const g = new Gate();
    this.#gates.set(tool, [...(this.#gates.get(tool) ?? []), g]);
    this.#open.add(g);
    return g;
  }

  /** Fail every gate still holding (cleanup). */
  releaseAll(): void {
    for (const g of this.#open) g.fail("scenario cleanup");
    this.#open.clear();
    this.#gates.clear();
  }
}

// ---------------------------------------------------------------------------
// Committed state
// ---------------------------------------------------------------------------

export class Inspector {
  private constructor(readonly storage: SessionStorage) {}

  static async open(env: PgTestEnv, sessionId: string): Promise<Inspector> {
    return new Inspector(await new PostgresStorageProvider(env.pool).open(sessionId));
  }

  async entries(conversationId: string) {
    return (await this.storage.listEntries(conversationId, { limit: 1000 })).items;
  }

  async submissions(conversationId: string) {
    return (await this.storage.listSubmissions(conversationId, { limit: 1000 })).items;
  }

  async tasks(conversationId?: string) {
    return (
      await this.storage.listTasks({
        limit: 1000,
        ...(conversationId === undefined ? {} : { conversationId }),
      })
    ).items;
  }

  async conversations() {
    return (await this.storage.listConversations({ limit: 1000 })).items;
  }

  async doc<T>(kind: string, scopeId: string): Promise<T | null> {
    return ((await this.storage.getDocument(kind, scopeId))?.data as T | undefined) ?? null;
  }

  /** Wait for a submission to settle (`done` or `unanswered`). */
  settled(id: string, timeoutMs = 30_000): Promise<Stored<SubmissionRecord>> {
    return until(
      async () => {
        const s = await this.storage.getSubmission(id);
        return s && (s.status === "done" || s.status === "unanswered") ? s : null;
      },
      `submission ${id} to settle`,
      timeoutMs,
    );
  }

  close(): Promise<void> {
    return this.storage.close();
  }
}

// ---------------------------------------------------------------------------
// The scenario fixture
// ---------------------------------------------------------------------------

export interface Scenario {
  readonly pg: PgTestEnv;
  readonly gateway: ScenarioGateway;
  /** Write the model script workers read (re-read per request). */
  script(script: ModelScript): Promise<void>;
  /** Start a test-harness worker on `sessionId`. */
  worker(sessionId: string, env?: Record<string, string>): WorkerProc;
  /** Start the production entry point on `sessionId`. */
  productionWorker(sessionId: string, env?: Record<string, string>): WorkerProc;
  inspect(sessionId: string): Promise<Inspector>;
}

/** Register hooks providing Postgres, a gateway, a script file and worker cleanup. */
export function useScenario(): Scenario {
  const pg = usePostgres();
  let gateway: ScenarioGateway | null = null;
  let dir = "";
  const procs: WorkerProc[] = [];
  const inspectors: Inspector[] = [];

  beforeAll(async () => {
    gateway = await ScenarioGateway.start();
    dir = await mkdtemp(join(tmpdir(), "oniva-scenario-"));
    await writeFile(join(dir, "model.json"), JSON.stringify({ rules: [] }));
  });

  afterEach(async () => {
    for (const p of procs.splice(0)) {
      if (p.child.signalCode === null && p.child.exitCode === null) {
        p.child.kill("SIGCONT");
        p.kill("SIGKILL");
      }
      await p.exited;
    }
    gateway?.releaseAll();
    for (const i of inspectors.splice(0)) await i.close().catch(() => {});
  });

  afterAll(async () => {
    await gateway?.gw.close();
    if (dir) await rm(dir, { recursive: true, force: true });
  });

  const baseEnv = (sessionId: string): Record<string, string> => ({
    DATABASE_URL: pg.url,
    ENGINE_DB_SCHEMA: pg.schema,
    ENGINE_SESSION_ID: sessionId,
    ENGINE_SERVICE_TOKEN: TOKEN,
    ENGINE_PORT: "0",
    ENGINE_HOST: "127.0.0.1",
    ENGINE_LEASE_TTL_MS: String(LEASE_TTL_MS),
    ENGINE_GATEWAY_URL: (gateway as ScenarioGateway).gw.baseUrl,
    ENGINE_GATEWAY_TOKEN: TOKEN,
  });

  return {
    pg,
    get gateway() {
      if (!gateway) throw new Error("scenario used before beforeAll");
      return gateway;
    },
    script: (script) => writeFile(join(dir, "model.json"), JSON.stringify(script)),
    worker(sessionId, env = {}) {
      const p = new WorkerProc("scripts/worker-test-harness.ts", {
        ...baseEnv(sessionId),
        ENGINE_TEST_HARNESS: "1",
        ENGINE_TEST_MODEL_SCRIPT: join(dir, "model.json"),
        ...env,
      });
      procs.push(p);
      return p;
    },
    productionWorker(sessionId, env = {}) {
      const p = new WorkerProc("scripts/worker.ts", { ...baseEnv(sessionId), ...env });
      procs.push(p);
      return p;
    },
    async inspect(sessionId) {
      const i = await Inspector.open(pg, sessionId);
      inspectors.push(i);
      return i;
    },
  };
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

export async function createConversation(url: string, agent: JsonObject = {}): Promise<string> {
  const res = await call(url, "POST", "/v1/conversations", { headers: AUTH, body: { agent } });
  if (res.status !== 201)
    throw new Error(`create conversation: ${res.status} ${JSON.stringify(res.body)}`);
  return res.body.conversationId as string;
}

export function submit(
  url: string,
  conversationId: string,
  body: JsonObject,
): Promise<JsonResponse> {
  return call(url, "POST", `/v1/conversations/${conversationId}/submissions`, {
    headers: AUTH,
    body,
  });
}

/** Payloads of a conversation's entries of one kind. */
export function payloads<T>(entries: { kind: string; payload: unknown }[], kind: string): T[] {
  return entries.filter((e) => e.kind === kind).map((e) => e.payload as T);
}
