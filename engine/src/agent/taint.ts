/**
 * Taint tracking (R7, KTD7).
 *
 * Once a conversation has shown the model output the host does not trust, that
 * fact is durable and permanent: it is recorded on the **root** conversation's
 * `agent.taint` document, so a subagent's child conversation taints the whole
 * tree and a policy that watches taint cannot be bypassed by delegating.
 *
 * A taint is added by exactly one kind of event, in the same commit that makes
 * the untrusted content visible to the model:
 * - `tool_result`: a tool call whose result taints (see {@link toolTaints}) committed
 *   its `tool_result` entry;
 * - `input`: a submission the host marked `untrusted: true` was placed into a
 *   run.
 *
 * The document keeps the first {@link TAINT_SOURCE_CAP} sources (for audit and
 * UI) and a `total` count of every event, so the cap never loses the fact that
 * a conversation is tainted.
 */
import { z } from "zod";
import { defineDoc } from "../session/documents.js";
import type { Session } from "../session/session.js";
import type { SessionReader, Transaction } from "../session/transaction.js";
import { INTERNAL_NATIVE_TOOLS } from "../tools/registry.js";

/** How many sources the taint document keeps (the `total` counts them all). */
export const TAINT_SOURCE_CAP = 20;

const sourceSchema = z.object({
  /** `tool_result` for a tool result, `input` for an untrusted submission. */
  kind: z.enum(["tool_result", "input"]),
  /** The tool name, or `"input"`. */
  name: z.string(),
  /** The tool call id, or the submission id. */
  ref: z.string(),
  /** Injected-clock epoch ms. */
  at: z.number(),
});

export type TaintSource = z.infer<typeof sourceSchema>;

export type TaintState = {
  tainted: boolean;
  /** Every taint event, including those past the source cap. */
  total: number;
  /** The first {@link TAINT_SOURCE_CAP} sources, oldest first. */
  sources: TaintSource[];
};

export const taintDoc = defineDoc<TaintState>({
  kind: "agent.taint",
  scope: "conversation",
  initial: { tainted: false, total: 0, sources: [] },
  schema: z.object({
    tainted: z.boolean(),
    total: z.number(),
    sources: z.array(sourceSchema),
  }) as unknown as z.ZodType<TaintState>,
});

/** The subset of a tool's shape that decides whether its result taints. */
export interface TaintRelevantTool {
  name: string;
  source?: string;
  internal?: boolean;
  untrustedOutput?: boolean;
}

/**
 * Whether a tool call's result taints the conversation. A gateway tool taints
 * only when the manifest marks it `untrustedOutput`; any other (native) tool
 * taints unless it is flagged `internal` or is one of
 * {@link INTERNAL_NATIVE_TOOLS}.
 */
export function toolTaints(tool: TaintRelevantTool): boolean {
  if (tool.internal === true) return false;
  if (INTERNAL_NATIVE_TOOLS.includes(tool.name)) return false;
  return tool.untrustedOutput === true || tool.source !== "gateway";
}

/**
 * Mark `rootConversationId` tainted, in the caller's commit. Mutates the
 * document draft in place (like `approvals.ts`), so it is stored with whatever
 * else that commit writes.
 */
export async function markTainted(
  tx: Transaction,
  rootConversationId: string,
  source: { kind: TaintSource["kind"]; name: string; ref: string },
): Promise<void> {
  const draft = await tx.doc(taintDoc, rootConversationId);
  draft.value.tainted = true;
  draft.value.total += 1;
  if (draft.value.sources.length < TAINT_SOURCE_CAP) {
    draft.value.sources.push({ ...source, at: tx.now });
  }
}

/** The committed taint document of a root conversation. */
export function taintOf(session: Session, rootConversationId: string): Promise<TaintState> {
  return session.document(taintDoc, rootConversationId);
}

/** A taint summary as plain JSON (the value handed to `authorizeTool`). */
export function taintSummary(state: TaintState): TaintState {
  return structuredClone(state) as unknown as TaintState;
}

/**
 * The top-level conversation above `rec`: follow each owning task to its
 * conversation until a conversation has no owner. A broken or cyclic chain
 * stops at the last conversation reached.
 */
export async function rootConversationId(
  reader: SessionReader,
  rec: { id: string; ownerTaskId: string | null },
): Promise<string> {
  let id = rec.id;
  let owner = rec.ownerTaskId;
  const seen = new Set([id]);
  while (owner !== null) {
    const task = await reader.getTask(owner);
    if (!task || seen.has(task.conversationId)) break;
    id = task.conversationId;
    seen.add(id);
    const parent = await reader.getConversation(id);
    owner = parent ? parent.ownerTaskId : null;
  }
  return id;
}
