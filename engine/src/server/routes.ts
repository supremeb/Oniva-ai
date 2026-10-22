/**
 * The engine's service routes (R19). Each handler receives a
 * {@link RouteContext} that has already authenticated the caller and checked
 * session ownership (see `http.ts`); conversation routes then check the
 * tenant binding through `ctx.conversation()`.
 *
 * - `POST /v1/conversations` creates a conversation bound to the caller's
 *   tenant and project. Body: `{ id?, agent? }`. → 201
 * - `POST /v1/conversations/:id/submissions` submits an input. Body:
 *   `{ text? | content?, requestId?, whenBusy?, type? }`; an `Idempotency-Key`
 *   header stands in for a missing `requestId`. A repeat with the same
 *   request id returns the same submission. → 202
 * - `DELETE /v1/conversations/:id/submissions/:submissionId` cancels a
 *   still-queued input, settling it `unanswered(withdrawn)`. → 200
 *   `{ conversationId, submissionId, cancelled: true }`; 404 for an unknown
 *   submission, 409 `not_queued` when it already started or settled.
 * - `GET /v1/conversations/:id/watch` streams watch frames over SSE (`id:` is
 *   the cursor; resumes from `Last-Event-ID` or `?cursor=`).
 * - `POST /v1/conversations/:id/abort` aborts queued and running work.
 *   Body: `{ reason? }`. → 200
 * - `POST /v1/conversations/:id/approvals/:toolTaskId/decision` decides an
 *   approval. Body: `{ decision: "approve" | "deny", note? }`; the principal
 *   comes from `X-Oniva-Principal`. → 200
 * - `GET /v1/conversations/:id/questions` → 200 `{ conversationId, questions }`,
 *   the conversation's questions document items.
 * - `POST /v1/conversations/:id/questions/:toolTaskId/answer` answers an
 *   `ask_user` question. Body: `{ answer: { optionIds?, text? } }`; principal
 *   from `X-Oniva-Principal`. → 200 `{ question }`; 400 invalid, 404 not_owned,
 *   409 already_answered / not_waiting, 410 expired.
 * - `GET /v1/conversations/:id/trace` → 200 `ConversationTrace`: the entries of
 *   the conversation and of every subagent child below it, usage, taint and
 *   compactions. Read-only. `?limit=` (entries) and `?cursor=` page it: a
 *   response that does not hold everything has `truncated: true` and
 *   `nextCursor`. 400 for a bad limit or cursor.
 * - `PUT /v1/conversations/:id/agent` updates the agent configuration. Body:
 *   a partial agent config (unknown or mistyped fields → 400, nothing
 *   committed); it applies from the next model request and reaches watchers
 *   as a patch to `/agent`. → 200 `{ conversationId, agent }`
 * - `GET /v1/health` (no auth).
 */
import type { IncomingMessage, ServerResponse } from "node:http";
import { type ApprovalDecisionKind, type ApprovalItem, approvalsDoc } from "../agent/approvals.js";
import type { Harness } from "../agent/harness.js";
import {
  type AgentConfigInput,
  agentConfigDoc,
  applyAgentConfig,
  parseAgentConfigInput,
} from "../agent/prompt.js";
import { type QuestionItem, questionsDoc } from "../agent/questions.js";
import { normalizeSubmit, type SubmitInput } from "../agent/submission.js";
import { rootConversationId } from "../agent/taint.js";
import { StorageRequestError } from "../core/errors.js";
import type { JsonObject, JsonValue } from "../core/json.js";
import type { Session } from "../session/session.js";
import { type ConversationRecord, MAX_PAGE_LIMIT } from "../storage/types.js";
import { SignalRejected, type TaskDefinition } from "../tasks/define.js";
import type { WatchHub } from "../watch/stream.js";
import { buildTrace, InvalidTraceCursor } from "../watch/trace.js";
import { bindingMatches, headerValue, ONIVA_HEADERS, type TenantBinding } from "./auth.js";
import { roleAllowsLane, type SessionRole } from "./role.js";

/** The engine API version reported by `GET /v1/health` (KTD16). */
export const ENGINE_API_VERSION = 1;

/** An error answered with `status` and `{ error, reason? }`. */
export class HttpError extends Error {
  constructor(
    readonly status: number,
    readonly error: string,
    readonly reason?: string,
    readonly extra: JsonObject = {},
  ) {
    super(reason ? `${error}: ${reason}` : error);
    this.name = "HttpError";
  }
}

export interface RouteContext {
  readonly harness: Harness;
  readonly hub: WatchHub;
  readonly req: IncomingMessage;
  readonly res: ServerResponse;
  readonly url: URL;
  readonly params: Readonly<Record<string, string>>;
  readonly ownerId: string;
  /** The role of this worker's session (`all` unless sharded). */
  readonly role: SessionRole;
  /** The request body as a JSON object (`{}` when empty). 413 / 400 on failure. */
  readJson(): Promise<JsonObject>;
  /** The caller's tenant and project headers (400 when missing). */
  caller(): TenantBinding;
  /** The `:id` conversation, checked against the caller's binding (404 / 403). */
  conversation(): Promise<ConversationRecord>;
  /** Whether this worker owns the session. */
  isOwner(): Promise<boolean>;
  /** Stream watch frames to the response until either side closes. */
  stream(start: { conversationId: string; cursor: number | null }): Promise<void>;
}

export type RouteResult = { status: number; body: JsonObject } | null;

export interface Route {
  method: "GET" | "POST" | "PUT" | "DELETE";
  /** Path segments; `:name` captures one. */
  pattern: string;
  auth: boolean;
  handle(ctx: RouteContext): Promise<RouteResult>;
}

const bad = (reason: string): HttpError => new HttpError(400, "bad_request", reason);

/** 409 `wrong_session_role` when this session's role does not take conversations in the agent's lane (default `interactive`). */
function assertLane(ctx: RouteContext, agent: unknown): void {
  const raw = (agent as { lane?: unknown } | undefined | null)?.lane;
  const lane = raw === "background" ? "background" : "interactive";
  if (!roleAllowsLane(ctx.role, lane)) {
    throw new HttpError(
      409,
      "wrong_session_role",
      `this session serves ${ctx.role} work, not ${lane}`,
      { role: ctx.role },
    );
  }
}

function optionalString(body: JsonObject, key: string): string | undefined {
  const value = body[key];
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") throw bad(`${key} must be a string`);
  return value;
}

function optionalBoolean(body: JsonObject, key: string): boolean | undefined {
  const value = body[key];
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "boolean") throw bad(`${key} must be a boolean`);
  return value;
}

function submitInput(ctx: RouteContext, body: JsonObject): SubmitInput {
  const input: SubmitInput = {};
  const text = optionalString(body, "text");
  if (text !== undefined) input.text = text;
  if (body.content !== undefined) {
    input.content = body.content as unknown as NonNullable<SubmitInput["content"]>;
  }
  const requestId =
    optionalString(body, "requestId") ?? headerValue(ctx.req, "idempotency-key") ?? undefined;
  if (requestId !== undefined) input.requestId = requestId;
  const whenBusy = optionalString(body, "whenBusy");
  if (whenBusy !== undefined) input.whenBusy = whenBusy as SubmitInput["whenBusy"] & string;
  const type = optionalString(body, "type");
  if (type !== undefined) input.type = type as SubmitInput["type"] & string;
  const untrusted = optionalBoolean(body, "untrusted");
  if (untrusted !== undefined) input.untrusted = untrusted;
  try {
    normalizeSubmit(input);
  } catch (err) {
    throw bad((err as Error).message);
  }
  return input;
}

/** The id of a conversation's root task of `kind`, or null. */
function rootTaskId(ctx: RouteContext, conversationId: string, kind: string): string | null {
  return (
    ctx.harness.runtime
      .tasks({ conversationId })
      .find((t) => t.kind === kind && t.parentTaskId === null)?.id ?? null
  );
}

/**
 * The ids of `rootId` and every conversation below it (a subagent's child
 * conversations), found by walking each conversation's owner chain.
 */
async function conversationTree(session: Session, rootId: string): Promise<string[]> {
  const ids = [rootId];
  for (let cursor: string | null = null; ; ) {
    const page = await session.reader.listConversations({ limit: MAX_PAGE_LIMIT, cursor });
    for (const rec of page.items) {
      if (rec.id === rootId) continue;
      if ((await rootConversationId(session.reader, rec)) === rootId) ids.push(rec.id);
    }
    cursor = page.nextCursor;
    if (cursor === null) return ids;
  }
}

/** Approval items for a conversation and its subagent descendants, oldest first. */
async function approvalsForRoot(harness: Harness, rootId: string): Promise<ApprovalItem[]> {
  const items: ApprovalItem[] = [];
  for (const id of await conversationTree(harness.session, rootId)) {
    items.push(...(await harness.session.document(approvalsDoc, id)).items);
  }
  items.sort((a, b) => a.requestedAt - b.requestedAt || a.id.localeCompare(b.id));
  return items;
}

/** Question items for a conversation and its subagent descendants, oldest first. */
async function questionsForRoot(harness: Harness, rootId: string): Promise<QuestionItem[]> {
  const items: QuestionItem[] = [];
  for (const id of await conversationTree(harness.session, rootId)) {
    items.push(...(await harness.session.document(questionsDoc, id)).items);
  }
  items.sort((a, b) => a.requestedAt - b.requestedAt || a.id.localeCompare(b.id));
  return items;
}

/**
 * The conversation in `rootId`'s tree that holds `toolTaskId`'s approval item,
 * so a decision sent to the root reaches a subagent child's call. Falls back to
 * `rootId` when the task is unknown or not below it (the decision is then
 * rejected as `not_owned`).
 */
async function approvalOwner(
  harness: Harness,
  rootId: string,
  toolTaskId: string,
): Promise<string> {
  const task = harness.runtime.tasks().find((t) => t.id === toolTaskId);
  if (!task || task.conversationId === rootId) return rootId;
  const tree = await conversationTree(harness.session, rootId);
  return tree.includes(task.conversationId) ? task.conversationId : rootId;
}

/** The conversation in `rootId`'s tree that owns `toolTaskId`; `rootId` when unknown (rejected `not_owned`). */
const questionOwner = approvalOwner;

/** Parse a resume cursor: a non-negative integer, or null when absent. */
export function parseCursor(raw: string | null): number | null {
  if (raw === null || raw.trim() === "") return null;
  const value = raw.trim();
  if (!/^(0|[1-9][0-9]*)$/.test(value) || !Number.isSafeInteger(Number(value))) {
    throw bad("cursor must be a non-negative integer");
  }
  return Number(value);
}

export const ROUTES: readonly Route[] = Object.freeze([
  {
    method: "GET",
    pattern: "/v1/health",
    auth: false,
    async handle(ctx) {
      return {
        status: 200,
        body: {
          status: "ok",
          sessionId: ctx.harness.session.sessionId,
          ownerId: ctx.ownerId,
          owner: await ctx.isOwner(),
          apiVersion: ENGINE_API_VERSION,
          role: ctx.role,
          taskKinds: [...ctx.harness.runtime.registry.kinds()].sort(),
        },
      };
    },
  },
  {
    method: "POST",
    pattern: "/v1/conversations",
    auth: true,
    async handle(ctx) {
      const body = await ctx.readJson();
      const caller = ctx.caller();
      const id = optionalString(body, "id");
      if (id !== undefined && id.length === 0) throw bad("id must not be empty");
      const agent = body.agent;
      if (
        agent !== undefined &&
        (typeof agent !== "object" || agent === null || Array.isArray(agent))
      ) {
        throw bad("agent must be an object");
      }
      assertLane(ctx, agent);
      let conversationId: string;
      try {
        const conv = await ctx.harness.createConversation({
          tenantId: caller.tenantId,
          projectId: caller.projectId,
          ...(id === undefined ? {} : { id }),
          ...(agent === undefined ? {} : { agent: agent as unknown as AgentConfigInput }),
        });
        conversationId = conv.id;
      } catch (err) {
        if (err instanceof StorageRequestError) {
          throw new HttpError(409, "conflict", err.message);
        }
        throw err;
      }
      return {
        status: 201,
        body: { conversationId, tenantId: caller.tenantId, projectId: caller.projectId },
      };
    },
  },
  {
    method: "POST",
    pattern: "/v1/conversations/:id/submissions",
    auth: true,
    async handle(ctx) {
      const body = await ctx.readJson();
      const conv = await ctx.conversation();
      const input = submitInput(ctx, body);
      const submission = await ctx.harness.conversation(conv.id).submit(input);
      const record = await submission.record();
      return {
        status: 202,
        body: {
          submissionId: submission.id,
          conversationId: conv.id,
          requestId: record?.requestId ?? null,
          status: record?.status ?? null,
        },
      };
    },
  },
  {
    method: "DELETE",
    pattern: "/v1/conversations/:id/submissions/:submissionId",
    auth: true,
    async handle(ctx) {
      const conv = await ctx.conversation();
      const submissionId = ctx.params.submissionId;
      if (submissionId === undefined) throw bad("submissionId");
      const outcome = await ctx.harness.conversation(conv.id).cancelSubmission(submissionId);
      if (outcome === "unknown") throw new HttpError(404, "not_found", "submission");
      if (outcome === "not_queued") throw new HttpError(409, "not_queued");
      return {
        status: 200,
        body: { conversationId: conv.id, submissionId, cancelled: true },
      };
    },
  },
  {
    method: "PUT",
    pattern: "/v1/conversations/:id/agent",
    auth: true,
    async handle(ctx) {
      const body = await ctx.readJson();
      const conv = await ctx.conversation();
      const parsed = parseAgentConfigInput(body);
      if (!parsed.ok) throw bad(parsed.reason);
      if (parsed.value.lane !== undefined) assertLane(ctx, parsed.value);
      const agent = await ctx.harness.conversation(conv.id).configure(parsed.value);
      return {
        status: 200,
        body: { conversationId: conv.id, agent: agent as unknown as JsonObject },
      };
    },
  },
  {
    method: "GET",
    pattern: "/v1/conversations/:id/watch",
    auth: true,
    async handle(ctx) {
      const conv = await ctx.conversation();
      const cursor = parseCursor(
        headerValue(ctx.req, "last-event-id") ?? ctx.url.searchParams.get("cursor"),
      );
      await ctx.stream({ conversationId: conv.id, cursor });
      return null;
    },
  },
  {
    method: "POST",
    pattern: "/v1/conversations/:id/abort",
    auth: true,
    async handle(ctx) {
      const body = await ctx.readJson();
      const conv = await ctx.conversation();
      const reason = optionalString(body, "reason");
      await ctx.harness.conversation(conv.id).abort(reason);
      return { status: 200, body: { conversationId: conv.id, aborted: true } };
    },
  },
  {
    method: "GET",
    pattern: "/v1/conversations/:id/approvals",
    auth: true,
    async handle(ctx) {
      const conv = await ctx.conversation();
      const approvals = await approvalsForRoot(ctx.harness, conv.id);
      return {
        status: 200,
        body: { conversationId: conv.id, approvals: approvals as unknown as JsonObject },
      };
    },
  },
  {
    method: "POST",
    pattern: "/v1/conversations/:id/approvals/:toolTaskId/decision",
    auth: true,
    async handle(ctx) {
      const body = await ctx.readJson();
      const conv = await ctx.conversation();
      const toolTaskId = ctx.params.toolTaskId;
      if (toolTaskId === undefined) throw bad("toolTaskId");
      const principal = headerValue(ctx.req, ONIVA_HEADERS.principal);
      if (principal === null) {
        throw new HttpError(400, "missing_header", ONIVA_HEADERS.principal);
      }
      const note = body.note;
      const approval = await ctx.harness.decideApproval({
        conversationId: await approvalOwner(ctx.harness, conv.id, toolTaskId),
        toolTaskId,
        decision: body.decision as ApprovalDecisionKind,
        principal,
        ...(note === undefined ? {} : { note: note as string | null }),
      });
      return { status: 200, body: { approval: approval as unknown as JsonObject } };
    },
  },
  {
    method: "POST",
    pattern: "/v1/runs",
    auth: true,
    async handle(ctx) {
      const body = await ctx.readJson();
      const caller = ctx.caller();
      const id = optionalString(body, "id");
      if (id === undefined || id.length === 0) throw bad("id must be a non-empty string");
      const kind = optionalString(body, "kind");
      if (kind === undefined || kind.length === 0) throw bad("kind must be a non-empty string");
      const def = ctx.harness.runtime.registry.get(kind);
      if (!def) throw bad(`unknown task kind ${kind}`);
      const agent = body.agent;
      if (
        agent !== undefined &&
        (typeof agent !== "object" || agent === null || Array.isArray(agent))
      ) {
        throw bad("agent must be an object");
      }
      assertLane(ctx, agent);

      // The caller's id makes the start idempotent: a repeat returns the existing run.
      const existing = await ctx.harness.session.reader.getConversation(id);
      if (existing) {
        if (!bindingMatches(existing, caller)) throw new HttpError(403, "forbidden");
        return { status: 200, body: { conversationId: id, taskId: rootTaskId(ctx, id, kind) } };
      }
      const input = (body.input ?? null) as JsonValue;
      // A kind may check its input up front (a workflow graph); nothing is created for a bad one.
      const problem = (
        def as { validateInput?: (input: unknown) => string | null }
      ).validateInput?.(input);
      if (problem) throw bad(problem);
      let taskId: string;
      try {
        taskId = await ctx.harness.runtime.commit(async (tx, ops) => {
          const conv = ops.createConversation({
            tenantId: caller.tenantId,
            projectId: caller.projectId,
            id,
          });
          if (agent !== undefined) {
            const draft = await tx.doc(agentConfigDoc, conv.id);
            draft.set(applyAgentConfig(agentConfigDoc.initial, agent as AgentConfigInput));
          }
          return ops.spawn(def as TaskDefinition<JsonValue, JsonValue>, {
            conversationId: conv.id,
            parentTaskId: null,
            input,
          }).id;
        });
      } catch (err) {
        // A concurrent create won the conversation: return its run.
        if (err instanceof StorageRequestError) {
          return { status: 200, body: { conversationId: id, taskId: rootTaskId(ctx, id, kind) } };
        }
        throw err;
      }
      return { status: 201, body: { conversationId: id, taskId } };
    },
  },
  {
    method: "POST",
    pattern: "/v1/conversations/:id/tasks/:taskId/signal",
    auth: true,
    async handle(ctx) {
      const body = await ctx.readJson();
      const conv = await ctx.conversation();
      const taskId = ctx.params.taskId as string;
      const key = optionalString(body, "key");
      if (key === undefined || key.length === 0) throw bad("key must be a non-empty string");
      const task = ctx.harness.runtime.task(taskId);
      if (!task || task.conversationId !== conv.id) {
        throw new HttpError(404, "not_found", "task");
      }
      try {
        await ctx.harness.runtime.signal(taskId, key, (body.payload ?? null) as JsonValue);
      } catch (err) {
        if (err instanceof SignalRejected) throw new HttpError(409, "signal_rejected");
        throw err;
      }
      return { status: 200, body: { conversationId: conv.id, taskId, delivered: true } };
    },
  },
  {
    method: "GET",
    pattern: "/v1/conversations/:id/tasks/:taskId",
    auth: true,
    async handle(ctx) {
      const conv = await ctx.conversation();
      const taskId = ctx.params.taskId as string;
      const task = ctx.harness.runtime.task(taskId);
      if (!task || task.conversationId !== conv.id) {
        throw new HttpError(404, "not_found", "task");
      }
      return {
        status: 200,
        body: {
          id: task.id,
          kind: task.kind,
          status: task.status,
          phase: task.phase,
          ...(task.outcome === null ? {} : { outcome: task.outcome as unknown as JsonObject }),
        },
      };
    },
  },
  {
    method: "GET",
    pattern: "/v1/conversations/:id/questions",
    auth: true,
    async handle(ctx) {
      const conv = await ctx.conversation();
      const questions = await questionsForRoot(ctx.harness, conv.id);
      return {
        status: 200,
        body: { conversationId: conv.id, questions: questions as unknown as JsonObject },
      };
    },
  },
  {
    method: "GET",
    pattern: "/v1/conversations/:id/trace",
    auth: true,
    async handle(ctx) {
      const conv = await ctx.conversation();
      const rawLimit = ctx.url.searchParams.get("limit");
      let limit: number | undefined;
      if (rawLimit !== null) {
        if (!/^[1-9][0-9]*$/.test(rawLimit) || !Number.isSafeInteger(Number(rawLimit))) {
          throw bad("limit must be a positive integer");
        }
        limit = Number(rawLimit);
      }
      try {
        const trace = await buildTrace(ctx.harness.session, conv.id, {
          ...(limit === undefined ? {} : { limit }),
          cursor: ctx.url.searchParams.get("cursor"),
        });
        return { status: 200, body: trace as unknown as JsonObject };
      } catch (err) {
        if (err instanceof InvalidTraceCursor) throw bad(err.message);
        throw err;
      }
    },
  },
  {
    method: "POST",
    pattern: "/v1/conversations/:id/questions/:toolTaskId/answer",
    auth: true,
    async handle(ctx) {
      const body = await ctx.readJson();
      const conv = await ctx.conversation();
      const toolTaskId = ctx.params.toolTaskId;
      if (toolTaskId === undefined) throw bad("toolTaskId");
      const principal = headerValue(ctx.req, ONIVA_HEADERS.principal);
      if (principal === null) {
        throw new HttpError(400, "missing_header", ONIVA_HEADERS.principal);
      }
      const question = await ctx.harness.answerQuestion({
        conversationId: await questionOwner(ctx.harness, conv.id, toolTaskId),
        toolTaskId,
        answer: body.answer as { optionIds?: string[]; text?: string },
        principal,
      });
      return { status: 200, body: { question: question as unknown as JsonObject } };
    },
  },
]);

export type RouteMatch =
  | { route: Route; params: Record<string, string> }
  | { allowed: string[] }
  | null;

/** Find the route for a method and path; `{ allowed }` when only the method is wrong. */
export function matchRoute(method: string, pathname: string): RouteMatch {
  const segments = pathname.split("/").filter((s, i) => i > 0 || s !== "");
  const allowed: string[] = [];
  for (const route of ROUTES) {
    const parts = route.pattern.split("/").filter((s, i) => i > 0 || s !== "");
    if (parts.length !== segments.length) continue;
    const params: Record<string, string> = {};
    let ok = true;
    for (let i = 0; i < parts.length && ok; i++) {
      const part = parts[i] as string;
      const seg = segments[i] as string;
      if (part.startsWith(":")) {
        let decoded: string;
        try {
          decoded = decodeURIComponent(seg);
        } catch {
          return null;
        }
        if (decoded.length === 0) ok = false;
        else params[part.slice(1)] = decoded;
      } else if (part !== seg) {
        ok = false;
      }
    }
    if (!ok) continue;
    if (route.method === method) {
      return { route, params };
    }
    allowed.push(route.method);
  }
  return allowed.length > 0 ? { allowed } : null;
}
