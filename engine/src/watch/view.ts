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
  /** Partial output by tool task id. */
  toolOutput: { [toolTaskId: string]: ToolOutputState };
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
  const toolOutput: ConversationView["toolOutput"] = {};
  for (let cursor: string | null = null; ; ) {
    const page = await reader.listTasks({ conversationId, limit: MAX_PAGE_LIMIT, cursor });
    const ids = page.items.filter((t) => t.kind === TOOL_TASK_KIND).map((t) => t.id);
    // One batched read per page of tasks (a page holds at most MAX_PAGE_LIMIT).
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
  };
  return { view, toolTasks };
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
  const { view, toolTasks } = state;
  const ops: JsonPatchOp[] = [];
  for (const task of event.batch.createTasks ?? []) {
    if (task.conversationId === conversationId && task.kind === TOOL_TASK_KIND) {
      toolTasks.add(task.id);
    }
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
