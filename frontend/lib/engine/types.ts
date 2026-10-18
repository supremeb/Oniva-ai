/**
 * Wire types mirroring engine/src/watch/{view,stream,patch}.ts.
 * Wire data is checked by the hand-written guards below, never cast.
 */

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonPatchOp =
  | { op: 'add'; path: string; value: JsonValue }
  | { op: 'replace'; path: string; value: JsonValue }
  | { op: 'remove'; path: string };

export interface EntryRecord {
  id: string;
  conversationId: string;
  taskId: string | null;
  kind: string;
  payload: JsonValue;
  createdAt: number;
}

export interface LiveState {
  runId: string | null;
  taskId: string | null;
  /** The streaming assistant message, or null when idle. */
  message: JsonValue;
}

export interface InboxQueueItem {
  submissionId: string;
  mode: 'followUp' | 'steer';
  /** First ~200 characters of the queued text. Absent on older engines. */
  preview?: string;
  /** The caller's request id. Absent on older engines. */
  requestId?: string;
}

export interface InboxState {
  queue: InboxQueueItem[];
  placed: { submissionId: string; runId: string }[];
}

export interface ApprovalItem {
  id: string;
  toolTaskId: string;
  runId: string;
  toolCallId: string;
  toolName: string;
  arguments: { [key: string]: JsonValue };
  reason: string | null;
  requestedAt: number;
  expiresAt: number | null;
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'cancelled';
  closedAt: number | null;
  [key: string]: JsonValue | undefined;
}

export interface ApprovalsState {
  items: ApprovalItem[];
}

export interface QuestionOptionItem {
  id: string;
  label: string;
  description: string | null;
}

export interface QuestionAnswerRecord {
  optionIds: string[];
  text: string | null;
  principal: string;
  answeredAt: number;
}

/** An `ask_user` question (engine/src/agent/questions.ts `QuestionItem`). */
export interface QuestionItem {
  id: string;
  toolTaskId: string;
  runId: string;
  toolCallId: string;
  question: string;
  options: QuestionOptionItem[];
  multiSelect: boolean;
  allowFreeText: boolean;
  requestedAt: number;
  expiresAt: number | null;
  status: 'pending' | 'answered' | 'expired' | 'cancelled';
  answer: QuestionAnswerRecord | null;
  closedAt: number | null;
}

export interface QuestionsState {
  items: QuestionItem[];
}

export interface UsageTally {
  requests: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  costUsd: number | null;
  [key: string]: JsonValue | undefined;
}

export interface UsageTotals {
  requests: number;
  total: UsageTally;
  byModel: { [model: string]: UsageTally };
  byPurpose: { [purpose: string]: UsageTally };
}

export interface ToolOutputState {
  text: string;
  truncated: boolean;
  /** The call this output belongs to; null for documents written before the link existed. */
  toolCallId?: string | null;
  toolName?: string | null;
  attempt?: number;
  [key: string]: JsonValue | undefined;
}

/** A subagent child as the parent's view shows it (engine/src/watch/view.ts `SubagentView`). */
export interface SubagentView {
  conversationId: string;
  submissionId: string;
  status: 'running' | 'done' | 'aborted' | 'failed';
  startedAt: string;
  endedAt?: string;
  toolCalls: number;
  /** Last five tool-call descriptions, oldest first (<= 160 chars each). */
  recent: string[];
  usage: UsageTally;
}

/** A host task as the view shows it (engine/src/watch/view.ts `TaskView`): workflow runs and their nodes. */
export interface TaskView {
  kind: string;
  /** The owning task, or null for a root task of the conversation. */
  parentId: string | null;
  status: 'pending' | 'running' | 'waiting' | 'done' | 'aborted' | 'faulted';
  phase: string;
  label?: string;
  attempt?: number;
  /** A workflow run's `{ nodeId: status }` summary (success, failed, blocked, skipped). */
  nodes?: { [nodeId: string]: string };
  /** ISO deadline of a waiting task that has one. */
  waitUntil?: string;
  startedAt: string;
  endedAt?: string;
  /** The abort reason or fault message of an aborted or faulted task. */
  error?: string;
}

export interface ConversationView {
  entries: EntryRecord[];
  live: LiveState;
  inbox: InboxState;
  approvals: ApprovalsState;
  /** Always present after parsing; `{ items: [] }` when an older engine omits it. */
  questions: QuestionsState;
  usage: UsageTotals;
  agent: { [key: string]: JsonValue };
  toolOutput: { [toolTaskId: string]: ToolOutputState };
  /** Whether a run is active, across tool rounds and approval waits. Absent on older engines. */
  run?: RunView;
  /** Subagent children by the parent's tool task id. Absent on older engines. */
  subagents?: { [toolTaskId: string]: SubagentView };
  /** Host tasks by task id (workflow runs and nodes). Absent on older engines. */
  tasks?: { [taskId: string]: TaskView };
}

export interface RunView {
  active: boolean;
  runId?: string;
  startedAt?: string;
}

export interface PatchFrame {
  cursor: number;
  patch: JsonPatchOp[];
}

export interface SnapshotFrame {
  cursor: number;
  snapshot: ConversationView;
}

export type WatchFrame = PatchFrame | SnapshotFrame;

export const isSnapshotFrame = (f: WatchFrame): f is SnapshotFrame => 'snapshot' in f;

export class WireError extends Error {
  constructor(what: string) {
    super(`malformed engine data: ${what}`);
    this.name = 'WireError';
  }
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);

function isJson(v: unknown): v is JsonValue {
  if (v === null) return true;
  switch (typeof v) {
    case 'boolean':
    case 'string':
      return true;
    case 'number':
      return Number.isFinite(v);
    case 'object':
      return Array.isArray(v) ? v.every(isJson) : Object.values(v as Obj).every(isJson);
    default:
      return false;
  }
}

function parseOp(raw: unknown): JsonPatchOp {
  if (!isObj(raw) || typeof raw.path !== 'string') throw new WireError('patch op');
  if (raw.op === 'remove') return { op: 'remove', path: raw.path };
  if (raw.op === 'add' || raw.op === 'replace') {
    if (!isJson(raw.value)) throw new WireError('patch value');
    return { op: raw.op, path: raw.path, value: raw.value };
  }
  throw new WireError(`patch op kind ${String(raw.op)}`);
}

/** Validate the structural skeleton of a view (not every nested leaf). */
export function parseConversationView(raw: unknown): ConversationView {
  if (!isObj(raw)) throw new WireError('view');
  const { entries, live, inbox, approvals, usage, agent, toolOutput } = raw;
  if (!Array.isArray(entries)) throw new WireError('view.entries');
  for (const e of entries) {
    if (!isObj(e) || typeof e.id !== 'string' || typeof e.kind !== 'string' || !isJson(e.payload)) {
      throw new WireError('view.entries[]');
    }
  }
  if (!isObj(live) || !('message' in live) || !isJson(live.message)) throw new WireError('view.live');
  if (!isObj(inbox) || !Array.isArray(inbox.queue) || !Array.isArray(inbox.placed)) {
    throw new WireError('view.inbox');
  }
  if (!isObj(approvals) || !Array.isArray(approvals.items)) throw new WireError('view.approvals');
  if (!isObj(usage) || !isObj(usage.total)) throw new WireError('view.usage');
  if (!isObj(agent) || !isJson(agent)) throw new WireError('view.agent');
  if (!isObj(toolOutput) || !isJson(toolOutput)) throw new WireError('view.toolOutput');
  if ('subagents' in raw && raw.subagents !== undefined && (!isObj(raw.subagents) || !isJson(raw.subagents))) {
    throw new WireError('view.subagents');
  }
  if ('tasks' in raw && raw.tasks !== undefined && (!isObj(raw.tasks) || !isJson(raw.tasks))) {
    throw new WireError('view.tasks');
  }
  // Skeleton verified above; the engine's zod schemas own the leaves.
  if ('questions' in raw && raw.questions !== undefined) {
    if (!isObj(raw.questions) || !Array.isArray(raw.questions.items)) throw new WireError('view.questions');
    return raw as unknown as ConversationView;
  }
  // Older engines have no questions document.
  return { ...raw, questions: { items: [] } } as unknown as ConversationView;
}

export function parseWatchFrame(raw: unknown): WatchFrame {
  if (!isObj(raw) || typeof raw.cursor !== 'number' || !Number.isInteger(raw.cursor)) {
    throw new WireError('frame cursor');
  }
  if ('snapshot' in raw) return { cursor: raw.cursor, snapshot: parseConversationView(raw.snapshot) };
  if (Array.isArray(raw.patch)) return { cursor: raw.cursor, patch: raw.patch.map(parseOp) };
  throw new WireError('frame has neither snapshot nor patch');
}
