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
import { type UsageTotals, usageDoc } from "../agent/usage.js";
import type { JsonValue } from "../core/json.js";
import { parseDoc } from "../session/documents.js";
import type { CommitEvent } from "../session/publish.js";
import type { Session } from "../session/session.js";
import { type EntryRecord, MAX_PAGE_LIMIT } from "../storage/types.js";
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
export async function loadViewState(session: Session, conversationId: string): Promise<ViewState> {
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
  const toolOutput: ConversationView["toolOutput"] = {};
  for (let cursor: string | null = null; ; ) {
    const page = await reader.listTasks({ conversationId, limit: MAX_PAGE_LIMIT, cursor });
    const ids = page.items.filter((t) => t.kind === TOOL_TASK_KIND).map((t) => t.id);
    // One batched read per page of tasks (a page holds at most MAX_PAGE_LIMIT).
    for (const t of page.items) {
      if (isRootGeneration(t)) {
        runTasks.set(t.id, { startedAt: t.createdAt, terminal: t.status === "terminal" });
      }
      if (t.kind === COMPACTION_TASK_KIND) compactionTasks.set(t.id, compactionInfo(t));
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
  };
  return { view, toolTasks, compactionTasks, runTasks };
}

/** Read a conversation's view from committed state. Throws `UnknownConversation`. */
export async function loadConversationView(
  session: Session,
  conversationId: string,
): Promise<ConversationView> {
  return (await loadViewState(session, conversationId)).view;
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
  const { view, toolTasks, compactionTasks, runTasks } = state;
  const ops: JsonPatchOp[] = [];
  for (const task of event.batch.createTasks ?? []) {
    if (task.conversationId === conversationId && task.kind === TOOL_TASK_KIND) {
      toolTasks.add(task.id);
    } else if (task.conversationId === conversationId && task.kind === COMPACTION_TASK_KIND) {
      compactionTasks.set(task.id, compactionInfo(task));
    }
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
  return ops;
}
