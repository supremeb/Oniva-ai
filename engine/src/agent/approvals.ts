/**
 * Approvals (R10, R19, KTD9): durable human decisions on tool calls.
 *
 * When `HostPolicy.authorizeTool` answers `requireApproval`, the tool task
 * appends an item to its conversation's `agent.approvals` document and, in the
 * same commit, parks in `waiting({ signal: "approval", until: expiresAt })`.
 * A long wait therefore holds no running task and survives restarts.
 *
 * `decideApproval` checks, in one commit, that the conversation owns the tool
 * task and that the request is still pending and unexpired; it then records
 * the decision with the principal it was given (verified by the caller, e.g.
 * the HTTP layer) and signals the task. Any other decision is rejected with
 * {@link ApprovalRejected}, including a second one.
 *
 * Outcomes, resolved by the tool task (`tool-task.ts`): `approve` commits the
 * intent and the tool executes once; `deny` yields a `denied` result; reaching
 * `expiresAt` first yields `approval_expired`. Every result of a call that
 * needed approval carries the approval (`ToolResultPayload.approval`).
 */
import { z } from "zod";
import type { JsonObject, JsonValue } from "../core/json.js";
import { defineDoc } from "../session/documents.js";
import type { Transaction } from "../session/transaction.js";
import { SignalRejected, type TaskOps } from "../tasks/define.js";
import { jsonValue } from "./prompt.js";

/** The signal key a tool task waits on for its decision. */
export const APPROVAL_SIGNAL = "approval";

export type ApprovalDecisionKind = "approve" | "deny";
export type ApprovalStatus = "pending" | "approved" | "denied" | "expired" | "cancelled";

const decisionSchema = z.object({
  decision: z.enum(["approve", "deny"]),
  /** Who decided, as verified by the caller. */
  principal: z.string().min(1),
  note: z.string().nullable(),
  /** Injected-clock epoch ms. */
  decidedAt: z.number(),
});

export type ApprovalDecision = z.infer<typeof decisionSchema>;

const itemSchema = z.object({
  id: z.string(),
  toolTaskId: z.string(),
  runId: z.string(),
  toolCallId: z.string(),
  toolName: z.string(),
  /** The final arguments the approval is for (after hooks). */
  arguments: z.record(z.string(), jsonValue),
  reason: z.string().nullable(),
  requestedAt: z.number(),
  /** Injected-clock epoch ms, or null for no expiry. */
  expiresAt: z.number().nullable(),
  status: z.enum(["pending", "approved", "denied", "expired", "cancelled"]),
  decision: decisionSchema.nullable(),
  /** When the item left `pending` (decided, expired or cancelled). */
  closedAt: z.number().nullable(),
});

export type ApprovalItem = z.infer<typeof itemSchema>;
export type ApprovalsState = { items: ApprovalItem[] };

export const approvalsDoc = defineDoc<ApprovalsState>({
  kind: "agent.approvals",
  scope: "conversation",
  initial: { items: [] },
  schema: z.object({ items: z.array(itemSchema) }) as unknown as z.ZodType<ApprovalsState>,
});

/** How an approval ended, as recorded on the call's `tool_result` entry. */
export type ToolResultApproval = {
  approvalId: string;
  status: "approved" | "denied" | "expired";
  principal: string | null;
  note: string | null;
  decidedAt: number;
};

export type ApprovalRejectedReason =
  /** Unknown tool task, or one the named conversation does not own. */
  | "not_owned"
  /** Already approved, denied or cancelled. */
  | "already_decided"
  | "expired"
  /** The call is not (or no longer) waiting for a decision. */
  | "not_waiting"
  /** Malformed input (decision, principal or note). */
  | "invalid";

/** A decision that was not accepted. Nothing was written. */
export class ApprovalRejected extends Error {
  readonly code = "approval_rejected";
  /** The item the rejection concerns, when the request was found (a snapshot). */
  readonly item: ApprovalItem | null;
  constructor(
    readonly reason: ApprovalRejectedReason,
    message: string,
    options?: ErrorOptions & { item?: ApprovalItem | null },
  ) {
    super(message, options);
    this.item = options?.item ?? null;
    this.name = "ApprovalRejected";
  }
}

export interface DecideApprovalInput {
  conversationId: string;
  toolTaskId: string;
  decision: ApprovalDecisionKind;
  /** The decider, verified by the caller against the conversation's binding. */
  principal: string;
  note?: string | null;
}

/** Append a pending item for a tool task. Returns the item. */
export async function requestApproval(
  tx: Transaction,
  input: {
    conversationId: string;
    toolTaskId: string;
    runId: string;
    toolCallId: string;
    toolName: string;
    arguments: JsonObject;
    reason: string | null;
    expiresAt: number | null;
  },
): Promise<ApprovalItem> {
  const draft = await tx.doc(approvalsDoc, input.conversationId);
  const existing = draft.value.items.find((i) => i.toolTaskId === input.toolTaskId);
  if (existing) return structuredClone(existing);
  const item: ApprovalItem = {
    id: tx.mintId("approval"),
    toolTaskId: input.toolTaskId,
    runId: input.runId,
    toolCallId: input.toolCallId,
    toolName: input.toolName,
    arguments: structuredClone(input.arguments),
    reason: input.reason,
    requestedAt: tx.now,
    expiresAt: input.expiresAt,
    status: "pending",
    decision: null,
    closedAt: null,
  };
  draft.value.items.push(item);
  return structuredClone(item);
}

/**
 * Close a tool task's still-pending item as `expired` or `cancelled`. Returns
 * the item (closed or not), or null if there is none.
 */
export async function closeApproval(
  tx: Transaction,
  conversationId: string,
  toolTaskId: string,
  status: "expired" | "cancelled",
): Promise<ApprovalItem | null> {
  const draft = await tx.doc(approvalsDoc, conversationId);
  const item = draft.value.items.find((i) => i.toolTaskId === toolTaskId);
  if (!item) return null;
  if (item.status === "pending") {
    item.status = status;
    item.closedAt = tx.now;
  }
  return structuredClone(item);
}

/** The committed item of a tool task, or null. */
export async function approvalOf(
  tx: Transaction,
  conversationId: string,
  toolTaskId: string,
): Promise<ApprovalItem | null> {
  const draft = await tx.doc(approvalsDoc, conversationId);
  const item = draft.value.items.find((i) => i.toolTaskId === toolTaskId);
  return item ? structuredClone(item) : null;
}

/** Read a decision signal payload, or null if it is not one. */
export function decisionFromSignal(payload: JsonValue | undefined): ApprovalDecision | null {
  const parsed = decisionSchema.safeParse(payload);
  return parsed.success ? parsed.data : null;
}

function checkInput(input: DecideApprovalInput): void {
  if (input.decision !== "approve" && input.decision !== "deny") {
    throw new ApprovalRejected("invalid", `decision must be "approve" or "deny"`);
  }
  if (typeof input.principal !== "string" || input.principal.length === 0) {
    throw new ApprovalRejected("invalid", "principal must be a non-empty string");
  }
  if (input.note !== undefined && input.note !== null && typeof input.note !== "string") {
    throw new ApprovalRejected("invalid", "note must be a string");
  }
  if (typeof input.conversationId !== "string" || typeof input.toolTaskId !== "string") {
    throw new ApprovalRejected("invalid", "conversationId and toolTaskId must be strings");
  }
}

/**
 * Inside one commit: verify, record the decision and signal the tool task.
 * Throws {@link ApprovalRejected} (rolling the commit back) when the decision
 * cannot be accepted.
 */
export async function applyDecision(
  tx: Transaction,
  ops: TaskOps,
  input: DecideApprovalInput,
  toolTaskKind: string,
): Promise<ApprovalItem> {
  checkInput(input);
  const { conversationId, toolTaskId } = input;
  const task = ops.task(toolTaskId);
  if (!task || task.kind !== toolTaskKind || task.conversationId !== conversationId) {
    throw new ApprovalRejected(
      "not_owned",
      `conversation ${conversationId} has no tool call ${toolTaskId}`,
    );
  }
  const draft = await tx.doc(approvalsDoc, conversationId);
  const item = draft.value.items.find((i) => i.toolTaskId === toolTaskId);
  if (!item) {
    throw new ApprovalRejected("not_waiting", `tool call ${toolTaskId} has no approval request`);
  }
  if (item.status === "expired" || (item.expiresAt !== null && tx.now >= item.expiresAt)) {
    throw new ApprovalRejected("expired", `the approval for ${toolTaskId} has expired`, {
      item: structuredClone(item),
    });
  }
  if (item.status !== "pending") {
    throw new ApprovalRejected(
      "already_decided",
      `the approval for ${toolTaskId} is already ${item.status}`,
      { item: structuredClone(item) },
    );
  }
  if (task.status !== "waiting" || task.waiting?.signal !== APPROVAL_SIGNAL) {
    throw new ApprovalRejected("not_waiting", `tool call ${toolTaskId} is not awaiting approval`, {
      item: structuredClone(item),
    });
  }
  const decision: ApprovalDecision = {
    decision: input.decision,
    principal: input.principal,
    note: input.note ?? null,
    decidedAt: tx.now,
  };
  item.status = input.decision === "approve" ? "approved" : "denied";
  item.decision = decision;
  item.closedAt = tx.now;
  try {
    ops.signal(toolTaskId, APPROVAL_SIGNAL, decision as unknown as JsonValue);
  } catch (err) {
    if (err instanceof SignalRejected) {
      throw new ApprovalRejected("not_waiting", err.message, { cause: err });
    }
    throw err;
  }
  return structuredClone(item);
}

/** The approval as recorded on a tool result. */
export function resultApproval(item: ApprovalItem, now: number): ToolResultApproval {
  return {
    approvalId: item.id,
    status:
      item.status === "approved" ? "approved" : item.status === "denied" ? "denied" : "expired",
    principal: item.decision?.principal ?? null,
    note: item.decision?.note ?? null,
    decidedAt: item.decision?.decidedAt ?? item.closedAt ?? now,
  };
}
