/**
 * viewToMessages: the adapter from the engine's conversation view to the chat
 * renderer's message shape (plan 2026-10-15-001, U6, KTD5/KTD6).
 *
 * Entry kinds are those of engine/src/agent/context-builder.ts:
 *   user         -> a user message
 *   assistant    -> thinking / text / tool_call parts of the turn's assistant message
 *   tool_result  -> completes the tool_call part with the same toolCallId, plus
 *                   derived file and source parts
 *   run_error    -> an error part (`retryable` from the engine's classification)
 *   compaction   -> a collapsed "Earlier messages summarized" info message
 *   system / unknown -> ignored (display-only)
 * `view.live.message` (the streaming assistant message) extends the trailing
 * assistant message; `view.toolOutput` feeds the running tool's progress step;
 * `view.approvals` marks calls that wait for a human as `pending` and adds an
 * `approval` part per request (a card while pending, a resolved line after).
 * `view.questions` does the same for `ask_user`: a pending question is a card the
 * viewer answers, an answered or expired one a read-only card, a cancelled one nothing.
 * `partial: true` assistant entries (text streamed before a failed generation)
 * render their text; their tool calls were never run and are skipped.
 *
 * Pure. `createViewAdapter()` memoizes per message, so a patch that touches one
 * message leaves every other message referentially equal.
 */
import type { MessagePart, SourceItem } from '@/lib/models';
import {
  buildErrorPart,
  buildTextPart,
  buildThinkingPart,
  buildToolCallPart,
  deriveFileParts,
  deriveSourceParts,
  parseToolResultContent,
} from '@/lib/chat/parts';
import type { ApprovalItem, ConversationView, EntryRecord, JsonValue, QuestionItem } from './types';
import { summarizeArguments } from './approval';

export interface EngineMessage {
  id: string;
  role: 'user' | 'assistant';
  /** Flat text (user text, or the assistant's text parts joined). */
  content: string;
  reasoning?: string;
  richParts?: MessagePart[];
  /** Source parts of the message, for the sources pill. */
  sources?: SourceItem[];
  /** True only for the message that receives `view.live`. */
  isStreaming: boolean;
  /** Entry ids this message was built from (empty for a live-only message). */
  entryIds: string[];
  createdAt: number | null;
  /** User messages: the client's request id, to match optimistic entries. */
  requestId?: string;
  /** Engine run of the (last) entry of an assistant message, to match the cost ledger. */
  runId?: string;
}

export interface AdapterOptions {
  /** UI hint from the viewer's role; the server enforces. Default true. */
  canDecide?: boolean;
}

// ---------------------------------------------------------------------------
// Payload readers. Wire data is untyped JSON; every read is guarded.
// ---------------------------------------------------------------------------

type Obj = { [key: string]: JsonValue };
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined);

/** Text of an engine `string | TextContent[]` value. */
function textOf(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((c) => (isObj(c) && c.type === 'text' ? (str(c.text) ?? '') : '')).join('');
  }
  return '';
}

interface Block {
  type: string;
  [key: string]: unknown;
}

function blocksOf(message: unknown): Block[] {
  if (!isObj(message) || !Array.isArray(message.blocks)) return [];
  return message.blocks.filter((b): b is Obj => isObj(b) && typeof b.type === 'string') as Block[];
}

interface ToolResult {
  entry: EntryRecord;
  status: string;
  content: string;
  taskId: string | null;
}

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

interface Group {
  id: string;
  kind: 'user' | 'assistant' | 'compaction';
  entries: EntryRecord[];
  trailing: boolean;
}

function groupEntries(entries: readonly EntryRecord[]): Group[] {
  const groups: Group[] = [];
  let current: Group | null = null;
  let anchor: string | null = null; // id of the user entry the assistant group answers
  for (const e of entries) {
    if (e.kind === 'user') {
      groups.push({ id: e.id, kind: 'user', entries: [e], trailing: false });
      current = null;
      anchor = e.id;
    } else if (e.kind === 'compaction') {
      groups.push({ id: e.id, kind: 'compaction', entries: [e], trailing: false });
      current = null;
      anchor = null;
    } else if (e.kind === 'assistant' || e.kind === 'tool_result' || e.kind === 'run_error') {
      if (current === null) {
        current = { id: anchor !== null ? `${anchor}:assistant` : e.id, kind: 'assistant', entries: [], trailing: false };
        groups.push(current);
      }
      current.entries.push(e);
    }
  }
  const last = groups[groups.length - 1];
  if (last && last.kind === 'assistant') last.trailing = true;
  return groups;
}

// ---------------------------------------------------------------------------
// Building one assistant message
// ---------------------------------------------------------------------------

interface Ctx {
  results: Map<string, ToolResult>;
  approvalByCall: Map<string, ApprovalItem>;
  questionByCall: Map<string, QuestionItem>;
  view: ConversationView;
  /** tool task ids already tied to a call (by result entry or approval). */
  linkedTasks: Set<string>;
  canDecide: boolean;
}

function buildCtx(view: ConversationView, canDecide: boolean): Ctx {
  const results = new Map<string, ToolResult>();
  const linkedTasks = new Set<string>();
  for (const e of view.entries) {
    if (e.kind !== 'tool_result' || !isObj(e.payload)) continue;
    const id = str(e.payload.toolCallId);
    if (id === undefined) continue;
    results.set(id, {
      entry: e,
      status: str(e.payload.status) ?? 'ok',
      content: textOf(e.payload.content),
      taskId: e.taskId,
    });
    if (e.taskId) linkedTasks.add(e.taskId);
  }
  const approvalByCall = new Map<string, ApprovalItem>();
  for (const a of view.approvals.items) {
    approvalByCall.set(a.toolCallId, a);
    linkedTasks.add(a.toolTaskId);
  }
  const questionByCall = new Map<string, QuestionItem>();
  for (const q of view.questions?.items ?? []) {
    questionByCall.set(q.toolCallId, q);
    linkedTasks.add(q.toolTaskId);
  }
  return { results, approvalByCall, questionByCall, view, linkedTasks, canDecide };
}

/** The status text the engine's non-ok results carry, for an error line. */
function errorText(r: ToolResult): string {
  const text = r.content.trim();
  return text !== '' ? text : `Tool call ${r.status}`;
}

interface CallInfo {
  id: string;
  name: string;
  args: Record<string, unknown> | undefined;
  assistantAt: number;
  /** Which later entries exist: true when something follows the call's assistant entry. */
  superseded: boolean;
}

function toolCallParts(call: CallInfo, ctx: Ctx, outputFor: (callId: string) => string | undefined): MessagePart[] {
  const result = ctx.results.get(call.id);
  if (result) {
    const ok = result.status === 'ok';
    const duration = result.entry.createdAt - call.assistantAt;
    const parsed = ok ? parseToolResultContent(result.content) : undefined;
    const parts: MessagePart[] = [
      buildToolCallPart({
        id: call.id,
        toolName: call.name,
        status: ok ? 'success' : 'error',
        args: call.args,
        result: ok ? parsed : undefined,
        error: ok ? undefined : errorText(result),
        durationMs: duration >= 0 ? duration : undefined,
      }),
    ];
    if (ok) {
      parts.push(...deriveSourceParts(call.name, parsed));
      parts.push(...deriveFileParts(call.name, call.id, parsed));
    }
    return parts;
  }

  const approval = ctx.approvalByCall.get(call.id);
  if (approval?.status === 'pending' || ctx.questionByCall.get(call.id)?.status === 'pending') {
    return [buildToolCallPart({ id: call.id, toolName: call.name, status: 'pending', args: call.args })];
  }
  if (call.superseded) {
    // The context builder gives such a call a synthesized "aborted" result.
    return [
      buildToolCallPart({
        id: call.id,
        toolName: call.name,
        status: 'error',
        args: call.args,
        error: 'The tool call was aborted before it produced a result.',
      }),
    ];
  }
  const out = outputFor(call.id);
  return [
    buildToolCallPart({
      id: call.id,
      toolName: call.name,
      status: 'running',
      args: call.args,
      steps:
        out !== undefined && out !== ''
          ? [{ id: `output-${call.id}`, label: 'Output', status: 'active', message: out }]
          : undefined,
    }),
  ];
}

function argsOf(block: Block): Record<string, unknown> | undefined {
  return isObj(block.arguments) && Object.keys(block.arguments).length > 0 ? (block.arguments as Obj) : undefined;
}

const isPartial = (e: EntryRecord): boolean => isObj(e.payload) && e.payload.partial === true;

function runIdOf(entries: readonly EntryRecord[]): string | undefined {
  for (let i = entries.length - 1; i >= 0; i--) {
    const p = (entries[i] as EntryRecord).payload;
    if (isObj(p) && typeof p.runId === 'string') return p.runId;
  }
  return undefined;
}

/** A pending request is a card; a decided one a resolved line; a cancelled one nothing. */
function approvalPart(a: ApprovalItem, canDecide: boolean): MessagePart | null {
  if (a.status === 'cancelled') return null;
  const status = a.status === 'denied' ? 'rejected' : a.status;
  const decision = isObj(a.decision) ? a.decision : null;
  const note = decision && typeof decision.note === 'string' ? decision.note : null;
  return {
    type: 'approval',
    id: a.toolTaskId,
    title: `Allow ${a.toolName}?`,
    ...(a.reason ? { description: a.reason } : {}),
    riskLevel: 'medium',
    items: summarizeArguments(a.arguments),
    status,
    toolTaskId: a.toolTaskId,
    toolName: a.toolName,
    canDecide,
    expiresAt: a.expiresAt,
    ...(note !== null ? { note } : {}),
  };
}

/** A pending question is a card; an answered or expired one a read-only card; a cancelled one nothing. */
function questionPart(q: QuestionItem, canDecide: boolean): MessagePart | null {
  if (q.status === 'cancelled') return null;
  return {
    type: 'question',
    id: q.toolTaskId,
    toolTaskId: q.toolTaskId,
    header: 'Question',
    question: q.question,
    options: q.options.map((o) => ({ id: o.id, label: o.label, ...(o.description ? { description: o.description } : {}) })),
    multiSelect: q.multiSelect,
    allowFreeText: q.allowFreeText,
    status: q.status,
    answer: q.answer ? { optionIds: q.answer.optionIds, text: q.answer.text } : null,
    canDecide,
    expiresAt: q.expiresAt,
  };
}

function buildAssistant(group: Group, ctx: Ctx, live: JsonValue | null): EngineMessage {
  const parts: MessagePart[] = [];
  const sources: SourceItem[] = [];
  const entries = group.entries;
  const lastAssistantIdx = entries.reduce((acc, e, i) => (e.kind === 'assistant' ? i : acc), -1);

  // Outputs carry their toolCallId: link exactly. Only documents without one
  // (written before the link existed) fall back to the one-call/one-output heuristic.
  const byCall = new Map<string, string>();
  const unlinked: string[] = [];
  for (const [taskId, out] of Object.entries(ctx.view.toolOutput)) {
    if (typeof out.toolCallId === 'string') byCall.set(out.toolCallId, out.text);
    else if (!ctx.linkedTasks.has(taskId)) unlinked.push(taskId);
  }
  const runningCalls: string[] = [];
  entries.forEach((e, i) => {
    if (e.kind !== 'assistant' || i !== lastAssistantIdx || !group.trailing || isPartial(e)) return;
    for (const b of blocksOf(isObj(e.payload) ? e.payload.message : null)) {
      if (b.type === 'tool_call' && typeof b.id === 'string' && !ctx.results.has(b.id)) runningCalls.push(b.id);
    }
  });
  const outputFor = (callId: string): string | undefined => {
    const exact = byCall.get(callId);
    if (exact !== undefined) return exact;
    const approval = ctx.approvalByCall.get(callId);
    if (approval) return ctx.view.toolOutput[approval.toolTaskId]?.text;
    if (runningCalls.length === 1 && runningCalls[0] === callId && unlinked.length === 1) {
      return ctx.view.toolOutput[unlinked[0] as string]?.text;
    }
    return undefined;
  };

  for (let i = 0; i < entries.length; i++) {
    const e = entries[i] as EntryRecord;
    if (e.kind === 'assistant' && isObj(e.payload)) {
      const superseded = group.trailing ? i !== lastAssistantIdx : true;
      for (const b of blocksOf(e.payload.message)) {
        if (b.type === 'thinking') {
          const t = str(b.thinking);
          if (t) parts.push(buildThinkingPart({ content: t, isThinking: false, showElapsed: true }));
        } else if (b.type === 'text') {
          const t = str(b.text);
          if (t) parts.push(buildTextPart(t, false));
        } else if (b.type === 'tool_call' && typeof b.id === 'string') {
          if (isPartial(e)) continue; // the generation failed: this call never ran
          const callParts = toolCallParts(
            { id: b.id, name: str(b.name) ?? 'tool', args: argsOf(b), assistantAt: e.createdAt, superseded },
            ctx,
            outputFor,
          );
          parts.push(...callParts);
        }
      }
    } else if (e.kind === 'run_error' && isObj(e.payload) && isObj(e.payload.error)) {
      const err = e.payload.error;
      parts.push(
        buildErrorPart({
          id: e.id,
          message: str(err.message) ?? 'The run failed.',
          title: 'Chat Error',
          retryable: typeof err.retryable === 'boolean' ? err.retryable : undefined,
        }),
      );
    }
  }

  // The streaming message extends the trailing assistant message.
  let isStreaming = false;
  if (live !== null && group.trailing) {
    isStreaming = true;
    const blocks = blocksOf(live);
    blocks.forEach((b, i) => {
      const lastBlock = i === blocks.length - 1;
      if (b.type === 'thinking') {
        const t = str(b.thinking);
        if (t) parts.push(buildThinkingPart({ content: t, isThinking: lastBlock, showElapsed: true }));
      } else if (b.type === 'text') {
        const t = str(b.text);
        if (t) parts.push(buildTextPart(t, lastBlock));
      } else if (b.type === 'tool_call' && typeof b.id === 'string') {
        // Arguments are still streaming: the call has not started.
        parts.push(buildToolCallPart({ id: b.id, toolName: str(b.name) ?? 'tool', status: 'pending', args: argsOf(b) }));
      }
    });
  }

  // Approval requests of this message's calls, after the activity feed.
  const callIds = new Set(callIdsOf(group));
  for (const a of ctx.view.approvals.items) {
    if (!callIds.has(a.toolCallId)) continue;
    const part = approvalPart(a, ctx.canDecide);
    if (part) parts.push(part);
  }
  for (const q of ctx.view.questions?.items ?? []) {
    if (!callIds.has(q.toolCallId)) continue;
    const part = questionPart(q, ctx.canDecide);
    if (part) parts.push(part);
  }

  for (const p of parts) {
    if (p.type === 'source') {
      sources.push({
        title: p.title,
        url: p.url ?? '',
        snippet: p.description,
        sourceType: p.sourceType as SourceItem['sourceType'],
      });
    }
  }
  const texts = parts.filter((p): p is Extract<MessagePart, { type: 'text' }> => p.type === 'text');
  const thoughts = parts.filter((p): p is Extract<MessagePart, { type: 'thinking' }> => p.type === 'thinking');
  const reasoning = thoughts.map((p) => p.thinkingContent ?? '').join('\n');
  const runId = runIdOf(entries);
  return {
    id: group.id,
    role: 'assistant',
    ...(runId !== undefined ? { runId } : {}),
    content: texts.map((p) => p.content).join(''),
    ...(reasoning ? { reasoning } : {}),
    richParts: parts,
    ...(sources.length > 0 ? { sources } : {}),
    isStreaming,
    entryIds: entries.map((e) => e.id),
    createdAt: entries[0]?.createdAt ?? null,
  };
}

function buildUser(entry: EntryRecord): EngineMessage {
  const p = isObj(entry.payload) ? entry.payload : {};
  const requestId = str(p.requestId);
  return {
    id: entry.id,
    role: 'user',
    content: textOf(p.content),
    isStreaming: false,
    entryIds: [entry.id],
    createdAt: entry.createdAt,
    ...(requestId !== undefined ? { requestId } : {}),
  };
}

function buildCompaction(entry: EntryRecord): EngineMessage {
  const summary = isObj(entry.payload) ? (str(entry.payload.summary) ?? '') : '';
  return {
    id: entry.id,
    role: 'assistant',
    content: '',
    richParts: [{ type: 'info', title: 'Earlier messages summarized', message: 'Earlier messages summarized', details: summary }],
    isStreaming: false,
    entryIds: [entry.id],
    createdAt: entry.createdAt,
  };
}

// ---------------------------------------------------------------------------
// Memoized adapter
// ---------------------------------------------------------------------------

const sameRefs = (a: readonly unknown[], b: readonly unknown[]) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

/**
 * Create an adapter that reuses a message when everything it was built from is
 * referentially unchanged (patch application keeps untouched subtrees' identity).
 */
export function createViewAdapter(options: AdapterOptions = {}): (view: ConversationView) => EngineMessage[] {
  const canDecide = options.canDecide !== false;
  let cache = new Map<string, { deps: unknown[]; message: EngineMessage }>();
  return (view) => {
    const groups = groupEntries(view.entries);
    const live = view.live.message;
    const ctx = buildCtx(view, canDecide);
    const next = new Map<string, { deps: unknown[]; message: EngineMessage }>();
    const out: EngineMessage[] = [];

    const emit = (id: string, deps: unknown[], build: () => EngineMessage) => {
      const hit = cache.get(id);
      const message = hit && sameRefs(hit.deps, deps) ? hit.message : build();
      next.set(id, { deps, message });
      out.push(message);
    };

    for (const g of groups) {
      if (g.kind === 'user') {
        emit(g.id, [g.entries[0]], () => buildUser(g.entries[0] as EntryRecord));
      } else if (g.kind === 'compaction') {
        emit(g.id, [g.entries[0]], () => buildCompaction(g.entries[0] as EntryRecord));
      } else {
        // Everything the message reads: its entries, the results and approvals
        // of its calls, and (only while trailing) the live message and outputs.
        const callIds = callIdsOf(g);
        const unresolved = callIds.some((id) => !ctx.results.has(id));
        // Being the last message only changes the output while a call is
        // unresolved or the live message is streaming into it.
        const live_ = g.trailing && live !== null;
        const deps: unknown[] = [(g.trailing && unresolved) || live_, ...g.entries];
        for (const callId of callIds) {
          deps.push(ctx.results.get(callId)?.entry, ctx.approvalByCall.get(callId), ctx.questionByCall.get(callId));
        }
        if (live_) deps.push(live);
        if (g.trailing && unresolved) deps.push(view.toolOutput, view.approvals, view.questions);
        emit(g.id, deps, () => buildAssistant(g, ctx, g.trailing ? live : null));
      }
    }

    // Live output with no committed assistant entry yet: a message of its own.
    const tail = groups[groups.length - 1];
    if (live !== null && (tail === undefined || tail.kind !== 'assistant')) {
      const id = `${tail?.kind === 'user' ? tail.id : 'live'}:assistant`;
      const synthetic: Group = { id, kind: 'assistant', entries: [], trailing: true };
      emit(id, ['live', live, view.toolOutput, view.approvals, view.questions], () => buildAssistant(synthetic, ctx, live));
    }
    cache = next;
    return out;
  };
}

/** Tool call ids an assistant group made. */
function callIdsOf(g: Group): string[] {
  const ids: string[] = [];
  for (const e of g.entries) {
    if (e.kind !== 'assistant' || isPartial(e)) continue;
    for (const b of blocksOf(isObj(e.payload) ? e.payload.message : null)) {
      if (b.type === 'tool_call' && typeof b.id === 'string') ids.push(b.id);
    }
  }
  return ids;
}

/** One-shot conversion (no memoization). */
export function viewToMessages(view: ConversationView): EngineMessage[] {
  return createViewAdapter()(view);
}
