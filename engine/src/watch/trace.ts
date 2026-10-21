/**
 * The trace of one conversation: its entries and those of every subagent
 * child below it, with usage, taint and compactions, in one read-only
 * structure. Built from committed state only (no lease, no commit), the way
 * `loadConversationView` is.
 *
 * Children are found by following each tool task's subagent link, depth first
 * in task order, down to `maxSubagentDepth`. A link to a conversation that is
 * unknown, already seen, or bound to another tenant or project is skipped, so
 * a trace never reaches data outside the root conversation's binding.
 *
 * Entries are paged by an entry budget across the whole tree. A page that does
 * not hold everything says `truncated: true` and carries `nextCursor`; calling
 * again with it continues exactly where the page stopped. The tree and its
 * order are recomputed on every call, so a cursor names a position in the
 * stable order (conversation index, entry cursor), not a snapshot.
 */

import { type CompactionPayload, ENTRY_KINDS } from "../agent/context-builder.js";
import { type AgentConfig, agentConfigDoc } from "../agent/prompt.js";
import { type TaintState, taintDoc } from "../agent/taint.js";
import { TOOL_TASK_KIND } from "../agent/tool-task.js";
import { type UsageTotals, usageDoc } from "../agent/usage.js";
import { DEFAULT_LIMITS } from "../core/limits.js";
import type { Session } from "../session/session.js";
import { type EntryRecord, MAX_PAGE_LIMIT, type TaskRecord } from "../storage/types.js";
import { maskSecrets } from "../worker/redact.js";
import { UnknownConversation } from "./view.js";

/** Entries per response when the caller does not say. */
export const DEFAULT_TRACE_ENTRIES = 2000;
/** The most entries one response may carry. */
export const MAX_TRACE_ENTRIES = 10_000;

/** A cursor that is not one this route issued. */
export class InvalidTraceCursor extends Error {
  constructor() {
    super("cursor is not a valid trace cursor");
    this.name = "InvalidTraceCursor";
  }
}

export interface TraceCompaction {
  entryId: string;
  conversationId: string;
  summaryChars: number;
}

export interface TraceAgent {
  depth: number;
  tools: string[];
  model: string | null;
  metadata: AgentConfig["metadata"];
}

export interface TraceChild {
  parentToolTaskId: string;
  /** The subagent tool's name. */
  role: string;
  /** The conversation that holds the spawning tool task. */
  parentConversationId: string;
  entries: EntryRecord[];
  usage: UsageTotals;
  agent: TraceAgent;
}

export interface ConversationTrace {
  conversationId: string;
  entries: EntryRecord[];
  children: { [conversationId: string]: TraceChild };
  /** Usage of the root conversation alone; a child's is on the child. */
  usage: UsageTotals["total"];
  taint: TaintState;
  compactions: TraceCompaction[];
  agent: TraceAgent;
  truncated: boolean;
  nextCursor: string | null;
}

export interface TraceOptions {
  /** Entries in this response, 1..{@link MAX_TRACE_ENTRIES}. */
  limit?: number;
  cursor?: string | null;
}

interface Node {
  id: string;
  parentToolTaskId: string;
  role: string;
  parentConversationId: string;
  depth: number;
}

interface Position {
  /** Index into the ordered conversations (0 = the root). */
  c: number;
  /** Entry cursor inside that conversation, or null for its start. */
  e: string | null;
}

function encodeCursor(p: Position): string {
  return Buffer.from(JSON.stringify(p), "utf8").toString("base64url");
}

function decodeCursor(raw: string | null | undefined): Position {
  if (raw === null || raw === undefined || raw === "") return { c: 0, e: null };
  try {
    const p = JSON.parse(Buffer.from(raw, "base64url").toString("utf8")) as Position;
    if (Number.isSafeInteger(p.c) && p.c >= 0 && (p.e === null || typeof p.e === "string")) {
      return { c: p.c, e: p.e };
    }
  } catch {
    // fall through
  }
  throw new InvalidTraceCursor();
}

const entryOf = (e: EntryRecord): EntryRecord => ({
  id: e.id,
  conversationId: e.conversationId,
  taskId: e.taskId,
  kind: e.kind,
  payload: maskSecrets(structuredClone(e.payload), {
    record: "entry",
    kind: e.kind,
    field: "payload",
  }) as EntryRecord["payload"],
  createdAt: e.createdAt,
});

function agentOf(config: AgentConfig): TraceAgent {
  return {
    depth: config.depth,
    tools: [...config.tools],
    model: config.model ?? null,
    metadata: maskSecrets(structuredClone(config.metadata), {
      record: "document",
      kind: agentConfigDoc.kind,
    }) as AgentConfig["metadata"],
  };
}

function subagentLink(task: Pick<TaskRecord, "checkpoint">): string | null {
  const link = (task.checkpoint as { subagent?: { conversationId?: unknown } } | null)?.subagent;
  return typeof link?.conversationId === "string" ? link.conversationId : null;
}

function roleOf(task: Pick<TaskRecord, "input">): string {
  const name = (task.input as { call?: { name?: unknown } } | null)?.call?.name;
  return typeof name === "string" ? name : "subagent";
}

/** The root and its descendants, depth first in task order. */
async function conversationNodes(session: Session, rootId: string): Promise<Node[]> {
  const reader = session.reader;
  const root = await reader.getConversation(rootId);
  if (!root) throw new UnknownConversation(rootId);
  const nodes: Node[] = [
    { id: rootId, parentToolTaskId: "", role: "", parentConversationId: "", depth: 0 },
  ];
  const seen = new Set([rootId]);
  const walk = async (id: string, depth: number): Promise<void> => {
    if (depth >= DEFAULT_LIMITS.maxSubagentDepth) return;
    for (let cursor: string | null = null; ; ) {
      const page = await reader.listTasks({ conversationId: id, limit: MAX_PAGE_LIMIT, cursor });
      for (const t of page.items) {
        if (t.kind !== TOOL_TASK_KIND) continue;
        const childId = subagentLink(t);
        if (childId === null || seen.has(childId)) continue;
        const child = await reader.getConversation(childId);
        if (!child || child.tenantId !== root.tenantId || child.projectId !== root.projectId) {
          continue;
        }
        seen.add(childId);
        nodes.push({
          id: childId,
          parentToolTaskId: t.id,
          role: roleOf(t),
          parentConversationId: id,
          depth: depth + 1,
        });
        await walk(childId, depth + 1);
      }
      cursor = page.nextCursor;
      if (cursor === null) return;
    }
  };
  await walk(rootId, 0);
  return nodes;
}

/** Build the trace of `rootId`. Throws `UnknownConversation` or {@link InvalidTraceCursor}. */
export async function buildTrace(
  session: Session,
  rootId: string,
  options: TraceOptions = {},
): Promise<ConversationTrace> {
  const start = decodeCursor(options.cursor);
  const limit = Math.min(Math.max(options.limit ?? DEFAULT_TRACE_ENTRIES, 1), MAX_TRACE_ENTRIES);
  const nodes = await conversationNodes(session, rootId);
  if (start.c >= nodes.length) throw new InvalidTraceCursor();

  const reader = session.reader;
  const entriesBy = new Map<string, EntryRecord[]>();
  const compactions: TraceCompaction[] = [];
  let budget = limit;
  let nextCursor: string | null = null;
  for (let ci = start.c; ci < nodes.length; ci++) {
    const node = nodes[ci] as Node;
    const list: EntryRecord[] = [];
    entriesBy.set(node.id, list);
    let cursor: string | null = ci === start.c ? start.e : null;
    for (;;) {
      if (budget <= 0) {
        nextCursor = encodeCursor({ c: ci, e: cursor });
        break;
      }
      const page = await reader.listEntries(node.id, {
        limit: Math.min(budget, MAX_PAGE_LIMIT),
        cursor,
      });
      list.push(...page.items.map(entryOf));
      budget -= page.items.length;
      cursor = page.nextCursor;
      if (cursor === null) break;
    }
    if (nextCursor !== null) break;
  }

  // Compactions are listed from the whole tree, whatever the page held.
  for (const node of nodes) {
    for (let cursor: string | null = null; ; ) {
      const page = await reader.listEntries(node.id, { limit: MAX_PAGE_LIMIT, cursor });
      for (const e of page.items) {
        if (e.kind !== ENTRY_KINDS.compaction) continue;
        const summary = (e.payload as unknown as CompactionPayload).summary;
        compactions.push({
          entryId: e.id,
          conversationId: node.id,
          summaryChars: typeof summary === "string" ? summary.length : 0,
        });
      }
      cursor = page.nextCursor;
      if (cursor === null) break;
    }
  }

  const children: ConversationTrace["children"] = {};
  for (const node of nodes.slice(1)) {
    const entries = entriesBy.get(node.id);
    if (entries === undefined) continue;
    children[node.id] = {
      parentToolTaskId: node.parentToolTaskId,
      role: node.role,
      parentConversationId: node.parentConversationId,
      entries,
      usage: await session.document(usageDoc, node.id),
      agent: agentOf(await session.document(agentConfigDoc, node.id)),
    };
  }
  const rootUsage = await session.document(usageDoc, rootId);
  return {
    conversationId: rootId,
    entries: entriesBy.get(rootId) ?? [],
    children,
    usage: rootUsage.total,
    taint: await session.document(taintDoc, rootId),
    compactions,
    agent: agentOf(await session.document(agentConfigDoc, rootId)),
    truncated: nextCursor !== null,
    nextCursor,
  };
}
