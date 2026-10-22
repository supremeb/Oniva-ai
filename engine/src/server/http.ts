/**
 * The engine's HTTP service on `node:http` (R19, R20, KTD4, KTD12).
 *
 * `createEngineServer` serves one harness (one session per worker). Every
 * route except `GET /v1/health` goes through, in order:
 *
 * 1. the bearer service token, compared in constant time → `401`;
 * 2. session ownership (`isOwner`) → `409 { error: "not_owner", ownerId }`;
 * 3. the body limit (`maxRequestBodyBytes`) → `413`;
 * 4. the `X-Oniva-Tenant` / `X-Oniva-Project` headers (`400` when missing),
 *    matched against the conversation's stored binding → `404` / `403`.
 *
 * Errors are JSON `{ error, reason? }`. Watch streams are Server-Sent Events:
 * each frame is sent as `id: <cursor>`, `event: snapshot|patch` and
 * `data: <frame JSON>`, with comment heartbeats; a slow client never holds
 * up commits (its frames collapse into a snapshot, see `watch/stream.ts`).
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { ApprovalRejected } from "../agent/approvals.js";
import type { Harness } from "../agent/harness.js";
import { QuestionRejected } from "../agent/questions.js";
import {
  ConversationBusy,
  LeaseLost,
  LimitExceeded,
  NotOwner,
  SessionFailed,
  StorageRequestError,
} from "../core/errors.js";
import type { JsonObject } from "../core/json.js";
import { type EngineLimits, resolveLimits } from "../core/limits.js";
import { InvalidDocument } from "../session/documents.js";
import { RuntimeStopped } from "../tasks/define.js";
import {
  isSnapshotFrame,
  WatchClosed,
  type WatchConsumer,
  WatchHub,
  type WatchOptions,
} from "../watch/stream.js";
import { UnknownConversation } from "../watch/view.js";
import { bearerMatches, bindingMatches, headerValue, ONIVA_HEADERS } from "./auth.js";
import type { SessionRole } from "./role.js";
import { HttpError, matchRoute, type RouteContext } from "./routes.js";

export interface EngineServerOptions {
  harness: Harness;
  /** The session this worker serves; must be the harness's (checked when given). */
  sessionId?: string;
  /** This worker's id (reported by health). */
  ownerId: string;
  /** The bearer token the backend presents. */
  serviceToken: string;
  /** The role of the session this worker serves. Default `all`. */
  role?: SessionRole;
  /** Overrides of the harness limits; the server uses `maxRequestBodyBytes`. */
  limits?: Partial<EngineLimits>;
  /** Whether this worker owns the session now. Default: the session is open. */
  isOwner?: () => boolean | Promise<boolean>;
  /** The current owner reported with a `409` when this worker is not it. */
  currentOwner?: () => string | null | Promise<string | null>;
  /** Watch stream settings (ignored when `hub` is given). */
  watch?: WatchOptions;
  /** A hub to share; by default the server opens (and closes) its own. */
  hub?: WatchHub;
  /** Interval of SSE heartbeat comments, in ms. Default 15 000. */
  heartbeatMs?: number;
  /** Receives unexpected errors (answered `500`). */
  onError?: (error: unknown) => void;
}

export interface EngineServer {
  readonly server: Server;
  readonly hub: WatchHub;
  /** Listen on `port` (0 for any free port). Resolves with the bound address. */
  listen(port?: number, host?: string): Promise<{ port: number; url: string }>;
  /** End watch streams, stop listening and drop connections. Leaves the harness open. */
  close(): Promise<void>;
}

export const DEFAULT_HEARTBEAT_MS = 15_000;

function sendJson(
  res: ServerResponse,
  status: number,
  body: JsonObject,
  headers: Record<string, string> = {},
): void {
  if (res.headersSent) {
    if (!res.writableEnded) res.end();
    return;
  }
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": String(Buffer.byteLength(text)),
    "cache-control": "no-store",
    ...headers,
  });
  res.end(text);
}

function errorBody(err: HttpError): JsonObject {
  return {
    error: err.error,
    ...(err.reason === undefined ? {} : { reason: err.reason }),
    ...err.extra,
  };
}

/** Read the body up to `limit` bytes; 413 beyond it. */
function readBody(req: IncomingMessage, limit: number): Promise<Buffer> {
  const declared = Number(req.headers["content-length"]);
  if (Number.isFinite(declared) && declared > limit) {
    req.resume();
    return Promise.reject(tooLarge(limit));
  }
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let failed = false;
    const onData = (chunk: Buffer) => {
      if (failed) return;
      size += chunk.length;
      if (size > limit) {
        failed = true;
        chunks.length = 0;
        reject(tooLarge(limit));
        return;
      }
      chunks.push(chunk);
    };
    req.on("data", onData);
    req.once("end", () => {
      if (!failed) resolve(Buffer.concat(chunks));
    });
    req.once("error", (err) => {
      if (!failed) reject(err);
      failed = true;
    });
  });
}

const tooLarge = (limit: number) =>
  new HttpError(413, "payload_too_large", `body exceeds ${limit} bytes`);

/** Map an error to a status and body; null for an unexpected one. */
function mapError(err: unknown): HttpError | null {
  if (err instanceof HttpError) return err;
  if (err instanceof UnknownConversation) return new HttpError(404, "not_found", "conversation");
  if (err instanceof ConversationBusy) return new HttpError(409, "conversation_busy");
  if (err instanceof ApprovalRejected) {
    return new HttpError(
      err.reason === "invalid" ? 400 : 409,
      "approval_rejected",
      err.reason,
      err.item === null ? {} : { approval: err.item as unknown as JsonObject },
    );
  }
  if (err instanceof QuestionRejected) {
    const status = { not_owned: 404, expired: 410, invalid: 400 }[err.reason as string] ?? 409;
    return new HttpError(
      status,
      "question_rejected",
      err.reason,
      err.item === null ? {} : { question: err.item as unknown as JsonObject },
    );
  }
  if (err instanceof NotOwner) {
    return new HttpError(409, "not_owner", undefined, { ownerId: err.ownerId ?? null });
  }
  if (err instanceof LeaseLost)
    return new HttpError(409, "not_owner", undefined, { ownerId: null });
  if (err instanceof LimitExceeded) return new HttpError(413, "limit_exceeded", err.limit);
  if (err instanceof InvalidDocument) return new HttpError(400, "bad_request", err.reason);
  // Storage refused this one write (a unique-key race such as two submits
  // with one requestId, or a record it would not accept). The session is
  // still healthy; a retry re-reads committed state (an idempotent submit
  // then finds the winner). The storage message is not echoed: it names ids.
  if (err instanceof StorageRequestError) return new HttpError(409, "conflict", "storage_request");
  if (err instanceof SessionFailed || err instanceof RuntimeStopped || err instanceof WatchClosed) {
    return new HttpError(503, "unavailable");
  }
  return null;
}

export function createEngineServer(options: EngineServerOptions): EngineServer {
  const { harness, ownerId, serviceToken } = options;
  if (typeof serviceToken !== "string" || serviceToken.length === 0) {
    throw new TypeError("serviceToken must be a non-empty string");
  }
  if (options.sessionId !== undefined && options.sessionId !== harness.session.sessionId) {
    throw new TypeError(
      `harness serves session ${harness.session.sessionId}, not ${options.sessionId}`,
    );
  }
  const limits = resolveLimits({ ...harness.limits, ...options.limits });
  const heartbeatMs = options.heartbeatMs ?? DEFAULT_HEARTBEAT_MS;
  if (!Number.isFinite(heartbeatMs) || heartbeatMs <= 0) {
    throw new RangeError("heartbeatMs must be a positive number");
  }
  const ownsHub = options.hub === undefined;
  const hub = options.hub ?? new WatchHub(harness, options.watch);
  const isOwner = options.isOwner ?? (() => harness.session.state === "open");
  const report = options.onError ?? (() => {});
  const streams = new Set<WatchConsumer>();

  async function stream(
    req: IncomingMessage,
    res: ServerResponse,
    start: { conversationId: string; cursor: number | null },
  ): Promise<void> {
    const consumer = await hub.watch(start.conversationId, { cursor: start.cursor });
    streams.add(consumer);
    const stop = () => consumer.close();
    req.once("close", stop);
    res.once("close", stop);
    res.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-store",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    });
    res.write(": connected\n\n");
    const heartbeat = setInterval(() => {
      if (!res.writableEnded) res.write(": ping\n\n");
    }, heartbeatMs);
    heartbeat.unref();
    try {
      for (;;) {
        const frame = await consumer.next();
        if (frame === null || res.writableEnded || res.destroyed) break;
        const event = isSnapshotFrame(frame) ? "snapshot" : "patch";
        const ok = res.write(
          `id: ${frame.cursor}\nevent: ${event}\ndata: ${JSON.stringify(frame)}\n\n`,
        );
        if (!ok) {
          // Wait for the socket; frames meanwhile buffer in the consumer.
          await new Promise<void>((resolve) => {
            const done = () => {
              res.off("drain", done);
              res.off("close", done);
              resolve();
            };
            res.on("drain", done);
            res.on("close", done);
          });
        }
      }
    } finally {
      clearInterval(heartbeat);
      req.off("close", stop);
      res.off("close", stop);
      consumer.close();
      streams.delete(consumer);
      if (!res.writableEnded) res.end();
    }
  }

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://engine.invalid");
    const match = matchRoute(req.method ?? "GET", url.pathname);
    const authorized = () =>
      bearerMatches(headerValue(req, "authorization") ?? undefined, serviceToken);

    if (match === null || "allowed" in match) {
      // Unknown paths are only revealed to authenticated callers.
      if (!authorized()) throw new HttpError(401, "unauthorized");
      if (match === null) throw new HttpError(404, "not_found", "route");
      throw new HttpError(405, "method_not_allowed", undefined, {});
    }
    const { route, params } = match;
    if (route.auth) {
      if (!authorized()) throw new HttpError(401, "unauthorized");
      if (!(await isOwner())) {
        const current = options.currentOwner ? await options.currentOwner() : null;
        throw new HttpError(409, "not_owner", undefined, { ownerId: current ?? null });
      }
    }

    let caller: { tenantId: string; projectId: string } | null = null;
    const ctx: RouteContext = {
      harness,
      hub,
      req,
      res,
      url,
      params,
      ownerId,
      role: options.role ?? "all",
      async readJson() {
        const raw = await readBody(req, limits.maxRequestBodyBytes);
        if (raw.length === 0) return {};
        let parsed: unknown;
        try {
          parsed = JSON.parse(raw.toString("utf8"));
        } catch {
          throw new HttpError(400, "bad_request", "body is not valid JSON");
        }
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          throw new HttpError(400, "bad_request", "body must be a JSON object");
        }
        return parsed as JsonObject;
      },
      caller() {
        if (caller) return caller;
        const tenantId = headerValue(req, ONIVA_HEADERS.tenant);
        const projectId = headerValue(req, ONIVA_HEADERS.project);
        if (tenantId === null) throw new HttpError(400, "missing_header", ONIVA_HEADERS.tenant);
        if (projectId === null) throw new HttpError(400, "missing_header", ONIVA_HEADERS.project);
        caller = { tenantId, projectId };
        return caller;
      },
      async conversation() {
        const binding = ctx.caller();
        const id = params.id as string;
        const rec = await harness.session.reader.getConversation(id);
        if (!rec) throw new HttpError(404, "not_found", "conversation");
        if (!bindingMatches(rec, binding)) throw new HttpError(403, "forbidden");
        return rec;
      },
      async isOwner() {
        return isOwner();
      },
      stream: (start) => stream(req, res, start),
    };
    const result = await route.handle(ctx);
    if (result) sendJson(res, result.status, result.body);
  }

  const server = createServer((req, res) => {
    handle(req, res).catch((err: unknown) => {
      const mapped = mapError(err);
      if (!mapped) report(err);
      const httpErr = mapped ?? new HttpError(500, "internal");
      const headers: Record<string, string> = {};
      if (httpErr.status === 401) headers["www-authenticate"] = "Bearer";
      if (httpErr.status === 405) {
        const m = matchRoute(req.method ?? "GET", new URL(req.url ?? "/", "http://x").pathname);
        if (m && "allowed" in m) headers.allow = m.allowed.join(", ");
      }
      // Do not keep reading an unread body on a reused connection.
      if (!req.complete) {
        headers.connection = "close";
        req.resume();
      }
      sendJson(res, httpErr.status, errorBody(httpErr), headers);
    });
  });

  return {
    server,
    hub,
    listen(port = 0, host = "127.0.0.1") {
      return new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(port, host, () => {
          server.off("error", reject);
          const addr = server.address() as AddressInfo;
          const shown = addr.family === "IPv6" ? `[${addr.address}]` : addr.address;
          resolve({ port: addr.port, url: `http://${shown}:${addr.port}` });
        });
      });
    },
    async close() {
      for (const consumer of [...streams]) consumer.close();
      if (ownsHub) await hub.close();
      await new Promise<void>((resolve) => {
        if (!server.listening) {
          resolve();
          return;
        }
        server.close(() => resolve());
        server.closeAllConnections();
      });
    },
  };
}
