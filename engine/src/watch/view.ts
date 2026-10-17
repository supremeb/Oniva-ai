/**
 * The committed-state view of one conversation (R17, KTD5).
 *
 * A view gathers everything a client renders: the entries, the streaming
 * `live` message, the inbox, approvals, usage totals, the agent
 * configuration, and the partial output of the conversation's tool calls
 * (keyed by tool task id, present once a tool has written output).
 *
 * `loadConversationView` reads it from committed state; `applyCommitToView`
 * advances a view by one commit event and returns the JSON Patch describing
 * the change, so a view loaded at seq N and advanced by the events after N
 * equals a view loaded later.
 */

import { type ApprovalsState, approvalsDoc } from "../agent/approvals.js";
import { COMPACTION_TASK_KIND, type CompactionState, compactionDoc } from "../agent/compaction.js";
import { GENERATION_TASK_KIND } from "../agent/generation.js";
import { type InboxState, inboxDoc } from "../agent/inbox.js";
import { type LiveState, liveDoc, type ToolOutputState, toolOutputDoc } from "../agent/progress.js";
import { type AgentConfig, agentConfigDoc } from "../agent/prompt.js";
import { TOOL_TASK_KIND } from "../agent/tool-task.js";
import { type UsageTally, type UsageTotals, usageDoc } from "../agent/usage.js";
import type { JsonValue } from "../core/json.js";
import { parseDoc } from "../session/documents.js";
import type { CommitEvent } from "../session/publish.js";
import type { Session } from "../session/session.js";
import {
  type EntryRecord,
  MAX_PAGE_LIMIT,
  type SubmissionRecord,
  type TaskRecord,
} from "../storage/types.js";
import { type TaskDefinition, type TaskRegistry, taskState } from "../tasks/define.js";
import { diffJson, escapePointerToken, type JsonPatchOp } from "./patch.js";

export interface ConversationView {
  entries: EntryRecord[];
  live: LiveState;
  inbox: InboxState;
  approvals: ApprovalsState;
  usage: UsageTotals;
  agent: AgentConfig;
  /** Partial output by tool task id; each carries the `toolCallId` / `toolName` it belongs to. */
  toolOutput: { [toolTaskId: string]: ToolOutputState };
  /** Whether a context compaction is in flight (derived from the `agent.compaction` document). */
  compaction: CompactionView;
  /** Whether the conversation has an active run (derived from its root generation task). */
  run: RunView;
  /**
   * Subagent children by the id of the parent's tool task that spawned them
   * (KTD2). Present once the child exists; `{}` for a conversation with none.
   */
  subagents: { [toolTaskId: string]: SubagentView };
  /**
   * Host tasks by task id, for kinds whose definition supplies a `view`
   * (workflow runs and their nodes); `{}` for a plain chat. Built-in kinds
   * (generation, tool, compaction) never appear.
   */
  tasks: { [taskId: string]: TaskView };
}

/** One host task as a client sees it. */
export interface TaskView {
  kind: string;
  /** The owning task, or null for a root task of the conversation. */
  parentId: string | null;
  status: "pending" | "running" | "waiting" | "done" | "aborted" | "faulted";
  phase: string;
  label?: string;
  attempt?: number;
  /** ISO deadline of a `waiting` task that has one. */
  waitUntil?: string;
  /** ISO time the task was created. */
  startedAt: string;
  /** ISO time the task finished. Only once terminal. */
  endedAt?: string;
  /** The abort reason or fault message of an `aborted` / `faulted` task. */
  error?: string;
}

function taskStatus(task: TaskRecord): TaskView["status"] {
  switch (task.status) {
    case "pending":
      return "pending";
    case "waiting":
      return "waiting";
    case "terminal":
      return task.outcome?.type === "aborted"
        ? "aborted"
        : task.outcome?.type === "faulted"
          ? "faulted"
          : "done";
    default:
      // `running` and `completing` (finishing its owned work) are both in progress.
      return "running";
  }
}

/** The view of a host task, or null when its kind has no `view`. A throwing `view` shows no details. */
function taskView(task: TaskRecord, def: TaskDefinition | undefined): TaskView | null {
  if (!def?.view) return null;
  let info: ReturnType<NonNullable<TaskDefinition["view"]>> = {};
  try {
    info = def.view(taskState(task) as never) ?? {};
  } catch {
    info = {};
  }
  const outcome = task.outcome;
  const error =
    outcome?.type === "aborted"
      ? outcome.reason
      : outcome?.type === "faulted"
        ? outcome.message
        : undefined;
  return {
    kind: task.kind,
    parentId: task.parentTaskId,
    status: taskStatus(task),
    phase: task.phase,
    ...(info.label === undefined ? {} : { label: info.label }),
    ...(info.attempt === undefined ? {} : { attempt: info.attempt }),
    ...(task.status === "waiting" && task.waiting?.until != null
      ? { waitUntil: new Date(task.waiting.until).toISOString() }
      : {}),
    startedAt: new Date(task.createdAt).toISOString(),
    ...(task.status === "terminal" ? { endedAt: new Date(task.updatedAt).toISOString() } : {}),
    ...(error === undefined ? {} : { error }),
  };
}

/** What the view reads of a session: itself, or a harness / hub source that also knows the task registry. */
export type ViewSource =
  | Session
  | { readonly session: Session; readonly runtime?: { readonly registry: TaskRegistry } };

/** A subagent child as the parent's view shows it: progress and recent activity, not its transcript. */
export interface SubagentView {
  conversationId: string;
  submissionId: string;
  /** `running` until the child's input settles; then `done`, `aborted` or `failed`. */
  status: "running" | "done" | "aborted" | "failed";
  /** ISO time the child's input was submitted. */
  startedAt: string;
  /** ISO time the child's input settled. Only once it has. */
  endedAt?: string;
  /** Tool calls the child's model has made so far. */
  toolCalls: number;
  /** The child's last {@link SUBAGENT_RECENT_CALLS} tool calls, oldest first: name and a short argument summary. */
  recent: string[];
  /** The child's own usage (excludes its descendants). */
  usage: UsageTally;
}

/** Tool calls kept in `SubagentView.recent`. */
export const SUBAGENT_RECENT_CALLS = 5;
/** Longest description in `SubagentView.recent`, in characters. */
export const SUBAGENT_CALL_CHARS = 160;

/** What the view tracks of one subagent child between commits. */
interface SubagentInfo {
  taskId: string;
  conversationId: string;
  submissionId: string;
  status: SubagentView["status"];
  createdAt: number;
  endedAt: number | null;
  toolCalls: number;
  recent: string[];
  usage: UsageTally;
}

/** The child a tool task spawned, from its checkpoint (absent for ordinary tools). */
function subagentLink(task: Pick<TaskRecord, "checkpoint">): {
  conversationId: string;
  submissionId: string;
} | null {
  const link = (task.checkpoint as { subagent?: unknown } | null)?.subagent as
    | { conversationId?: unknown; submissionId?: unknown }
    | null
    | undefined;
  return link && typeof link.conversationId === "string" && typeof link.submissionId === "string"
    ? { conversationId: link.conversationId, submissionId: link.submissionId }
    : null;
}

function submissionStatus(
  sub: Pick<SubmissionRecord, "status" | "result">,
): SubagentView["status"] {
  if (sub.status === "done") return "done";
  if (sub.status === "unanswered") {
    return (sub.result as { reason?: unknown } | null)?.reason === "aborted" ? "aborted" : "failed";
  }
  return "running";
}

function settled(sub: Pick<SubmissionRecord, "status">): boolean {
  return sub.status === "done" || sub.status === "unanswered";
}

/** `name(arguments)` clipped to {@link SUBAGENT_CALL_CHARS}. */
function describeCall(name: string, args: unknown): string {
  let summary = "";
  try {
    const json = JSON.stringify(args ?? {});
    summary = json === "{}" || json === undefined ? "" : json;
  } catch {
    summary = "";
  }
  const text = `${name}(${summary})`;
  return text.length <= SUBAGENT_CALL_CHARS
    ? text
    : `${text.slice(0, SUBAGENT_CALL_CHARS - 1)}\u2026`;
}

/** The descriptions of the tool calls an entry makes (assistant entries only; a partial one is skipped). */
function entryCalls(entry: Pick<EntryRecord, "kind" | "payload">): string[] {
  if (entry.kind !== "assistant") return [];
  const payload = entry.payload as { partial?: boolean; message?: { blocks?: unknown } };
  if (payload.partial === true || !Array.isArray(payload.message?.blocks)) return [];
  const out: string[] = [];
  for (const block of payload.message.blocks as Array<{
    type?: string;
    name?: string;
    arguments?: unknown;
  }>) {
    if (block.type === "tool_call" && typeof block.name === "string") {
      out.push(describeCall(block.name, block.arguments));
    }
  }
  return out;
}

function noteCalls(info: SubagentInfo, calls: string[]): void {
  if (calls.length === 0) return;
  info.toolCalls += calls.length;
  info.recent = [...info.recent, ...calls].slice(-SUBAGENT_RECENT_CALLS);
}

function applySubmission(info: SubagentInfo, sub: SubmissionRecord): void {
  info.status = submissionStatus(sub);
  info.endedAt = settled(sub) ? sub.updatedAt : null;
}

function subagentView(info: SubagentInfo): SubagentView {
  return {
    conversationId: info.conversationId,
    submissionId: info.submissionId,
    status: info.status,
    startedAt: new Date(info.createdAt).toISOString(),
    ...(info.endedAt === null ? {} : { endedAt: new Date(info.endedAt).toISOString() }),
    toolCalls: info.toolCalls,
    recent: [...info.recent],
    usage: structuredClone(info.usage),
  };
}

/** A conversation's run as a client sees it; true across model rounds, tool execution and approval waits. */
export interface RunView {
  active: boolean;
  /** The active run's id. Only while active. */
  runId?: string;
  /** ISO time the active run started. Only while active. */
  startedAt?: string;
}

/** A root generation task (one per run) as far as the view needs it. */
interface RunTaskInfo {
  startedAt: number;
  terminal: boolean;
}

function runView(tasks: ReadonlyMap<string, RunTaskInfo>): RunView {
  // Mirrors the harness's busy check: a non-terminal root generation task.
  for (const [id, t] of tasks) {
    if (!t.terminal)
      return { active: true, runId: id, startedAt: new Date(t.startedAt).toISOString() };
  }
  return { active: false };
}

function isRootGeneration(t: { kind: string; parentTaskId: string | null }): boolean {
  return t.kind === GENERATION_TASK_KIND && t.parentTaskId === null;
}

/** A conversation's compaction as a client sees it; no task ids or summary text. */
export interface CompactionView {
  running: boolean;
  /** Why it started: `background`, `threshold` or `overflow`. Only while running. */
  cause?: string;
  /** ISO time the running compaction started. Only while running. */
  startedAt?: string;
}

const IDLE_COMPACTION: CompactionView = Object.freeze({ running: false });

/** What the view needs of a compaction task, by task id. */
interface CompactionTaskInfo {
  cause?: string;
  startedAt: string;
}

function compactionInfo(t: { createdAt: number; input: unknown }): CompactionTaskInfo {
  const cause = (t.input as { cause?: unknown } | null)?.cause;
  return {
    ...(typeof cause === "string" ? { cause } : {}),
    startedAt: new Date(t.createdAt).toISOString(),
  };
}

function compactionView(
  doc: CompactionState,
  tasks: ReadonlyMap<string, CompactionTaskInfo>,
): CompactionView {
  if (doc.running === null) return { ...IDLE_COMPACTION };
  const info = tasks.get(doc.running);
  return {
    running: true,
    ...(info?.cause !== undefined ? { cause: info.cause } : {}),
    ...(info ? { startedAt: info.startedAt } : {}),
  };
}

type DocKey = "live" | "inbox" | "approvals" | "usage" | "agent";

/** Conversation-scoped documents in the view, by document kind. */
export const VIEW_DOCUMENTS: Readonly<Record<string, DocKey>> = Object.freeze({
  [liveDoc.kind]: "live",
  [inboxDoc.kind]: "inbox",
  [approvalsDoc.kind]: "approvals",
  [usageDoc.kind]: "usage",
  [agentConfigDoc.kind]: "agent",
});

/** No conversation with this id in the session. */
export class UnknownConversation extends Error {
  readonly code = "unknown_conversation";
  constructor(readonly conversationId: string) {
    super(`unknown conversation ${conversationId}`);
    this.name = "UnknownConversation";
  }
}

/** A view plus what is needed to advance it. */
export interface ViewState {
  view: ConversationView;
  /** Tool tasks of the conversation (whose output belongs to the view). */
  toolTasks: Set<string>;
  /** Compaction tasks of the conversation (their cause and start time). */
  compactionTasks: Map<string, CompactionTaskInfo>;
  /** Root generation tasks of the conversation (the runs). */
  runTasks: Map<string, RunTaskInfo>;
  /** Subagent children, by child conversation id. */
  subagents: Map<string, SubagentInfo>;
  /** The registry that says which kinds appear in `view.tasks`, if the source had one. */
  registry: TaskRegistry | null;
}

function entryOf(e: EntryRecord): EntryRecord {
  return {
    id: e.id,
    conversationId: e.conversationId,
    taskId: e.taskId,
    kind: e.kind,
    payload: structuredClone(e.payload),
    createdAt: e.createdAt,
  };
}

/** Read a conversation's view from committed state. */
export async function loadViewState(
  source: ViewSource,
  conversationId: string,
): Promise<ViewState> {
  const session = "session" in source ? source.session : source;
  const registry = ("session" in source ? source.runtime?.registry : undefined) ?? null;
  const reader = session.reader;
  if (!(await reader.getConversation(conversationId))) {
    throw new UnknownConversation(conversationId);
  }
  const entries: EntryRecord[] = [];
  for (let cursor: string | null = null; ; ) {
    const page = await reader.listEntries(conversationId, { limit: MAX_PAGE_LIMIT, cursor });
    entries.push(...page.items.map(entryOf));
    cursor = page.nextCursor;
    if (cursor === null) break;
  }
  const toolTasks = new Set<string>();
  const compactionTasks = new Map<string, CompactionTaskInfo>();
  const runTasks = new Map<string, RunTaskInfo>();
  const hostTasks: ConversationView["tasks"] = {};
  const toolOutput: ConversationView["toolOutput"] = {};
  const links: Array<{ taskId: string; conversationId: string; submissionId: string }> = [];
  for (let cursor: string | null = null; ; ) {
    const page = await reader.listTasks({ conversationId, limit: MAX_PAGE_LIMIT, cursor });
    const ids = page.items.filter((t) => t.kind === TOOL_TASK_KIND).map((t) => t.id);
    // One batched read per page of tasks (a page holds at most MAX_PAGE_LIMIT).
    for (const t of page.items) {
      if (t.kind === TOOL_TASK_KIND) {
        const link = subagentLink(t);
        if (link) links.push({ taskId: t.id, ...link });
      }
      if (isRootGeneration(t)) {
        runTasks.set(t.id, { startedAt: t.createdAt, terminal: t.status === "terminal" });
      }
      if (t.kind === COMPACTION_TASK_KIND) compactionTasks.set(t.id, compactionInfo(t));
      const hosted = taskView(t, registry?.get(t.kind));
      if (hosted) hostTasks[t.id] = hosted;
    }
    const docs = ids.length === 0 ? [] : await reader.getDocuments(toolOutputDoc.kind, ids);
    ids.forEach((id, i) => {
      toolTasks.add(id);
      const stored = docs[i];
      if (stored) toolOutput[id] = parseDoc(toolOutputDoc, id, stored.data);
    });
    cursor = page.nextCursor;
    if (cursor === null) break;
  }
  const subagents = new Map<string, SubagentInfo>();
  for (const link of links) {
    const sub = await reader.getSubmission(link.submissionId);
    if (!sub) continue;
    const info: SubagentInfo = {
      ...link,
      status: "running",
      createdAt: sub.createdAt,
      endedAt: null,
      toolCalls: 0,
      recent: [],
      usage: (await session.document(usageDoc, link.conversationId)).total,
    };
    applySubmission(info, sub);
    for (let cursor: string | null = null; ; ) {
      const page = await reader.listEntries(link.conversationId, {
        limit: MAX_PAGE_LIMIT,
        cursor,
      });
      for (const e of page.items) noteCalls(info, entryCalls(e));
      cursor = page.nextCursor;
      if (cursor === null) break;
    }
    subagents.set(link.conversationId, info);
  }
  const view: ConversationView = {
    entries,
    live: await session.document(liveDoc, conversationId),
    inbox: await session.document(inboxDoc, conversationId),
    approvals: await session.document(approvalsDoc, conversationId),
    usage: await session.document(usageDoc, conversationId),
    agent: await session.document(agentConfigDoc, conversationId),
    toolOutput,
    compaction: compactionView(
      await session.document(compactionDoc, conversationId),
      compactionTasks,
    ),
    run: runView(runTasks),
    subagents: Object.fromEntries([...subagents.values()].map((i) => [i.taskId, subagentView(i)])),
    tasks: hostTasks,
  };
  return { view, toolTasks, compactionTasks, runTasks, subagents, registry };
}

/** Read a conversation's view from committed state. Throws `UnknownConversation`. */
export async function loadConversationView(
  source: ViewSource,
  conversationId: string,
): Promise<ConversationView> {
  return (await loadViewState(source, conversationId)).view;
}

/**
 * Advance `state` (of `conversationId`) by one commit, in place. Returns the
 * patch from the previous view to the new one (empty when the commit does
 * not change the view).
 */
export function applyCommitToView(
  state: ViewState,
  conversationId: string,
  event: CommitEvent,
): JsonPatchOp[] {
  const { view, toolTasks, compactionTasks, runTasks, subagents, registry } = state;
  const ops: JsonPatchOp[] = [];
  const touched = new Set<SubagentInfo>();
  for (const task of event.batch.createTasks ?? []) {
    if (task.conversationId === conversationId && task.kind === TOOL_TASK_KIND) {
      toolTasks.add(task.id);
    } else if (task.conversationId === conversationId && task.kind === COMPACTION_TASK_KIND) {
      compactionTasks.set(task.id, compactionInfo(task));
    }
  }
  const submissions = [
    ...(event.batch.createSubmissions ?? []),
    ...(event.batch.replaceSubmissions ?? []),
  ];
  for (const task of [...(event.batch.createTasks ?? []), ...(event.batch.replaceTasks ?? [])]) {
    if (task.conversationId !== conversationId || task.kind !== TOOL_TASK_KIND) continue;
    const link = subagentLink(task);
    if (!link || subagents.has(link.conversationId)) continue;
    // The child is created in the commit that records the link: its input and
    // run start there, and nothing of it has happened yet.
    const sub = submissions.findLast((x) => x.id === link.submissionId);
    const info: SubagentInfo = {
      taskId: task.id,
      ...link,
      status: "running",
      createdAt: sub?.createdAt ?? task.updatedAt,
      endedAt: null,
      toolCalls: 0,
      recent: [],
      usage: structuredClone(usageDoc.initial.total),
    };
    if (sub) applySubmission(info, sub);
    subagents.set(link.conversationId, info);
    touched.add(info);
  }
  for (const sub of submissions) {
    for (const info of subagents.values()) {
      if (info.submissionId !== sub.id) continue;
      applySubmission(info, sub);
      touched.add(info);
    }
  }
  for (const task of [...(event.batch.createTasks ?? []), ...(event.batch.replaceTasks ?? [])]) {
    if (task.conversationId !== conversationId) continue;
    const next = taskView(task, registry?.get(task.kind));
    if (!next) continue;
    const path = `/tasks/${escapePointerToken(task.id)}`;
    if (Object.hasOwn(view.tasks, task.id)) {
      ops.push(
        ...diffJson(
          view.tasks[task.id] as unknown as JsonValue,
          next as unknown as JsonValue,
          path,
        ),
      );
    } else {
      ops.push({ op: "add", path, value: structuredClone(next) as unknown as JsonValue });
    }
    view.tasks[task.id] = next;
  }
  let runChanged = false;
  for (const task of event.batch.createTasks ?? []) {
    if (task.conversationId === conversationId && isRootGeneration(task)) {
      runTasks.set(task.id, { startedAt: task.createdAt, terminal: task.status === "terminal" });
      runChanged = true;
    }
  }
  for (const task of event.batch.replaceTasks ?? []) {
    const known = runTasks.get(task.id);
    if (known && task.conversationId === conversationId) {
      known.terminal = task.status === "terminal";
      runChanged = true;
    }
  }
  if (runChanged) {
    const next = runView(runTasks);
    ops.push(...diffJson(view.run as unknown as JsonValue, next as unknown as JsonValue, "/run"));
    view.run = next;
  }
  for (const entry of event.batch.appendEntries ?? []) {
    const child = subagents.get(entry.conversationId);
    if (child) {
      const calls = entryCalls(entry);
      if (calls.length > 0) {
        noteCalls(child, calls);
        touched.add(child);
      }
      continue;
    }
    if (entry.conversationId !== conversationId) continue;
    const value = entryOf(entry);
    ops.push({
      op: "add",
      path: `/entries/${view.entries.length}`,
      value: structuredClone(value) as unknown as JsonValue,
    });
    view.entries.push(value);
  }
  for (const change of event.documents) {
    const child = change.kind === usageDoc.kind ? subagents.get(change.scopeId) : undefined;
    if (child) {
      child.usage = structuredClone((change.after as unknown as UsageTotals).total);
      touched.add(child);
      continue;
    }
    const key = VIEW_DOCUMENTS[change.kind];
    if (key !== undefined && change.scopeId === conversationId) {
      ops.push(...diffJson(view[key] as JsonValue, change.after, `/${key}`));
      (view as unknown as Record<string, JsonValue>)[key] = structuredClone(change.after);
    } else if (change.kind === compactionDoc.kind && change.scopeId === conversationId) {
      const next = compactionView(change.after as unknown as CompactionState, compactionTasks);
      ops.push(
        ...diffJson(
          view.compaction as unknown as JsonValue,
          next as unknown as JsonValue,
          "/compaction",
        ),
      );
      view.compaction = next;
    } else if (change.kind === toolOutputDoc.kind && toolTasks.has(change.scopeId)) {
      const path = `/toolOutput/${escapePointerToken(change.scopeId)}`;
      const current = Object.hasOwn(view.toolOutput, change.scopeId)
        ? (view.toolOutput[change.scopeId] as JsonValue)
        : undefined;
      if (current === undefined) {
        ops.push({ op: "add", path, value: structuredClone(change.after) });
      } else {
        ops.push(...diffJson(current, change.after, path));
      }
      Object.defineProperty(view.toolOutput, change.scopeId, {
        value: structuredClone(change.after) as ToolOutputState,
        writable: true,
        enumerable: true,
        configurable: true,
      });
    }
  }
  for (const info of touched) {
    const path = `/subagents/${escapePointerToken(info.taskId)}`;
    const next = subagentView(info);
    if (Object.hasOwn(view.subagents, info.taskId)) {
      ops.push(
        ...diffJson(
          view.subagents[info.taskId] as unknown as JsonValue,
          next as unknown as JsonValue,
          path,
        ),
      );
    } else {
      ops.push({ op: "add", path, value: structuredClone(next) as unknown as JsonValue });
    }
    view.subagents[info.taskId] = next;
  }
  return ops;
}
