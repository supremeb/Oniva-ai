/**
 * Questions (R10): durable human answers to a model's question.
 *
 * A tool flagged `humanInput` (the native `ask_user`) does not execute. Once
 * its call is authorized, the tool task appends an item to its conversation's
 * `agent.questions` document and, in the same commit, parks in
 * `waiting({ signal: "answer", until: expiresAt })`. A long wait holds no
 * running task and survives restarts.
 *
 * `applyAnswer` checks, in one commit, that the conversation owns the tool
 * task, that the question is pending and unexpired and that the answer fits it
 * (known option ids, one unless `multiSelect`, free text only where allowed,
 * size-capped); it then records the answer with the principal it was given and
 * signals the task. Anything else is rejected with {@link QuestionRejected},
 * including a second answer.
 *
 * The model sees the answer as the tool result text (`answerText`): chosen
 * option labels, and free text as given. Reaching `expiresAt` first yields a
 * `question_expired` result and the run continues.
 */
import { z } from "zod";
import { type JsonValue, jsonByteLength } from "../core/json.js";
import { defineDoc } from "../session/documents.js";
import type { Transaction } from "../session/transaction.js";
import { SignalRejected, type TaskOps } from "../tasks/define.js";

/** The signal key a tool task waits on for its answer. */
export const ANSWER_SIGNAL = "answer";

/** Default question expiry: 7 days. */
export const DEFAULT_QUESTION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/** Most bytes of JSON an answer may take. */
export const MAX_ANSWER_BYTES = 16 * 1024;

export const MAX_QUESTION_OPTIONS = 20;

const optionSchema = z.object({
  id: z.string().min(1).max(64),
  label: z.string().min(1).max(200),
  description: z.string().max(500).optional(),
});

/** An option as stored: `description` is null when none was given. */
const storedOptionSchema = z.object({
  id: z.string(),
  label: z.string(),
  description: z.string().nullable(),
});

export type QuestionOption = z.infer<typeof storedOptionSchema>;

/** The arguments of a `humanInput` tool: what to ask. */
export const questionSpecSchema = z.object({
  question: z.string().min(1).max(2000),
  options: z
    .array(optionSchema)
    .max(MAX_QUESTION_OPTIONS)
    .refine((o) => new Set(o.map((x) => x.id)).size === o.length, "option ids must be unique")
    .optional(),
  /** More than one option may be chosen. */
  multiSelect: z.boolean().optional(),
  /** Free text may be given instead of (or beside) options. Always allowed without options. */
  allowFreeText: z.boolean().optional(),
});

export type QuestionSpec = z.infer<typeof questionSpecSchema>;

const answerSchema = z.object({
  optionIds: z.array(z.string()),
  text: z.string().nullable(),
  /** Who answered, as verified by the caller. */
  principal: z.string().min(1),
  /** Injected-clock epoch ms. */
  answeredAt: z.number(),
});

export type QuestionAnswer = z.infer<typeof answerSchema>;

const itemSchema = z.object({
  id: z.string(),
  toolTaskId: z.string(),
  runId: z.string(),
  toolCallId: z.string(),
  question: z.string(),
  options: z.array(storedOptionSchema),
  multiSelect: z.boolean(),
  allowFreeText: z.boolean(),
  requestedAt: z.number(),
  /** Injected-clock epoch ms, or null for no expiry. */
  expiresAt: z.number().nullable(),
  status: z.enum(["pending", "answered", "expired", "cancelled"]),
  answer: answerSchema.nullable(),
  /** When the item left `pending`. */
  closedAt: z.number().nullable(),
});

export type QuestionItem = z.infer<typeof itemSchema>;
export type QuestionStatus = QuestionItem["status"];
export type QuestionsState = { items: QuestionItem[] };

export const questionsDoc = defineDoc<QuestionsState>({
  kind: "agent.questions",
  scope: "conversation",
  initial: { items: [] },
  schema: z.object({ items: z.array(itemSchema) }) as unknown as z.ZodType<QuestionsState>,
});

export type QuestionRejectedReason =
  /** Unknown tool task, or one the named conversation does not own. */
  | "not_owned"
  /** Already answered or cancelled. */
  | "already_answered"
  | "expired"
  /** The call is not (or no longer) waiting for an answer. */
  | "not_waiting"
  /** Malformed input, or an answer that does not fit the question. */
  | "invalid";

/** An answer that was not accepted. Nothing was written. */
export class QuestionRejected extends Error {
  readonly code = "question_rejected";
  /** The item the rejection concerns, when it was found (a snapshot). */
  readonly item: QuestionItem | null;
  constructor(
    readonly reason: QuestionRejectedReason,
    message: string,
    options?: ErrorOptions & { item?: QuestionItem | null },
  ) {
    super(message, options);
    this.item = options?.item ?? null;
    this.name = "QuestionRejected";
  }
}

export interface AnswerQuestionInput {
  conversationId: string;
  toolTaskId: string;
  answer: { optionIds?: string[]; text?: string };
  /** The answerer, verified by the caller. */
  principal: string;
}

/** Append a pending item for a tool task. Returns the item. */
export async function requestQuestion(
  tx: Transaction,
  input: {
    conversationId: string;
    toolTaskId: string;
    runId: string;
    toolCallId: string;
    spec: QuestionSpec;
    expiresAt: number | null;
  },
): Promise<QuestionItem> {
  const draft = await tx.doc(questionsDoc, input.conversationId);
  const existing = draft.value.items.find((i) => i.toolTaskId === input.toolTaskId);
  if (existing) return structuredClone(existing);
  const options: QuestionOption[] = (input.spec.options ?? []).map((o) => ({
    id: o.id,
    label: o.label,
    description: o.description ?? null,
  }));
  const item: QuestionItem = {
    id: tx.mintId("question"),
    toolTaskId: input.toolTaskId,
    runId: input.runId,
    toolCallId: input.toolCallId,
    question: input.spec.question,
    options,
    multiSelect: input.spec.multiSelect ?? false,
    allowFreeText: options.length === 0 || (input.spec.allowFreeText ?? false),
    requestedAt: tx.now,
    expiresAt: input.expiresAt,
    status: "pending",
    answer: null,
    closedAt: null,
  };
  draft.value.items.push(item);
  return structuredClone(item);
}

/** Close a tool task's still-pending item as `expired` or `cancelled`. */
export async function closeQuestion(
  tx: Transaction,
  conversationId: string,
  toolTaskId: string,
  status: "expired" | "cancelled",
): Promise<QuestionItem | null> {
  const draft = await tx.doc(questionsDoc, conversationId);
  const item = draft.value.items.find((i) => i.toolTaskId === toolTaskId);
  if (!item) return null;
  if (item.status === "pending") {
    item.status = status;
    item.closedAt = tx.now;
  }
  return structuredClone(item);
}

/** The committed item of a tool task, or null. */
export async function questionOf(
  tx: Transaction,
  conversationId: string,
  toolTaskId: string,
): Promise<QuestionItem | null> {
  const draft = await tx.doc(questionsDoc, conversationId);
  const item = draft.value.items.find((i) => i.toolTaskId === toolTaskId);
  return item ? structuredClone(item) : null;
}

/** The answer as the model reads it: option labels, then free text as given. */
export function answerText(item: QuestionItem): string {
  const answer = item.answer;
  if (!answer) return "";
  const labels = answer.optionIds.map((id) => item.options.find((o) => o.id === id)?.label ?? id);
  const parts: string[] = [];
  if (labels.length > 0) parts.push(labels.join(", "));
  if (answer.text !== null) parts.push(answer.text);
  return parts.join("\n");
}

/** Whether a signal payload is an answer. */
export function isAnswerSignal(payload: JsonValue | undefined): boolean {
  return answerSchema.safeParse(payload).success;
}

const answerInputSchema = z
  .object({
    optionIds: z.array(z.string().min(1)).optional(),
    text: z.string().optional(),
  })
  .strict();

function parseInput(input: AnswerQuestionInput): {
  optionIds: string[];
  text: string | null;
} {
  if (typeof input.conversationId !== "string" || typeof input.toolTaskId !== "string") {
    throw new QuestionRejected("invalid", "conversationId and toolTaskId must be strings");
  }
  if (typeof input.principal !== "string" || input.principal.length === 0) {
    throw new QuestionRejected("invalid", "principal must be a non-empty string");
  }
  const parsed = answerInputSchema.safeParse(input.answer);
  if (!parsed.success) {
    throw new QuestionRejected("invalid", "answer must be { optionIds?: string[], text?: string }");
  }
  const { optionIds = [], text } = parsed.data;
  if (jsonByteLength(parsed.data) > MAX_ANSWER_BYTES) {
    throw new QuestionRejected("invalid", `answer is larger than ${MAX_ANSWER_BYTES} bytes`);
  }
  if (optionIds.length === 0 && (text === undefined || text.length === 0)) {
    throw new QuestionRejected("invalid", "answer must choose an option or give text");
  }
  return { optionIds, text: text === undefined || text.length === 0 ? null : text };
}

function checkFit(item: QuestionItem, optionIds: string[], text: string | null): void {
  if (new Set(optionIds).size !== optionIds.length) {
    throw new QuestionRejected("invalid", "optionIds must be unique");
  }
  for (const id of optionIds) {
    if (!item.options.some((o) => o.id === id)) {
      throw new QuestionRejected("invalid", `unknown option "${id}"`);
    }
  }
  if (optionIds.length > 1 && !item.multiSelect) {
    throw new QuestionRejected("invalid", "only one option may be chosen");
  }
  if (text !== null && !item.allowFreeText) {
    throw new QuestionRejected("invalid", "this question does not take free text");
  }
  if (optionIds.length === 0 && item.options.length > 0 && text === null) {
    throw new QuestionRejected("invalid", "choose an option");
  }
}

/**
 * Inside one commit: verify, record the answer and signal the tool task.
 * Throws {@link QuestionRejected} (rolling the commit back) when the answer
 * cannot be accepted.
 */
export async function applyAnswer(
  tx: Transaction,
  ops: TaskOps,
  input: AnswerQuestionInput,
  toolTaskKind: string,
): Promise<QuestionItem> {
  const { optionIds, text } = parseInput(input);
  const { conversationId, toolTaskId } = input;
  const task = ops.task(toolTaskId);
  if (!task || task.kind !== toolTaskKind || task.conversationId !== conversationId) {
    throw new QuestionRejected(
      "not_owned",
      `conversation ${conversationId} has no tool call ${toolTaskId}`,
    );
  }
  const draft = await tx.doc(questionsDoc, conversationId);
  const item = draft.value.items.find((i) => i.toolTaskId === toolTaskId);
  if (!item) {
    throw new QuestionRejected("not_waiting", `tool call ${toolTaskId} has no question`);
  }
  if (item.status === "expired" || (item.expiresAt !== null && tx.now >= item.expiresAt)) {
    throw new QuestionRejected("expired", `the question for ${toolTaskId} has expired`, {
      item: structuredClone(item),
    });
  }
  if (item.status !== "pending") {
    throw new QuestionRejected(
      "already_answered",
      `the question for ${toolTaskId} is already ${item.status}`,
      { item: structuredClone(item) },
    );
  }
  if (task.status !== "waiting" || task.waiting?.signal !== ANSWER_SIGNAL) {
    throw new QuestionRejected("not_waiting", `tool call ${toolTaskId} is not awaiting an answer`, {
      item: structuredClone(item),
    });
  }
  checkFit(item, optionIds, text);
  const answer: QuestionAnswer = {
    optionIds,
    text,
    principal: input.principal,
    answeredAt: tx.now,
  };
  item.status = "answered";
  item.answer = answer;
  item.closedAt = tx.now;
  try {
    ops.signal(toolTaskId, ANSWER_SIGNAL, answer as unknown as JsonValue);
  } catch (err) {
    if (err instanceof SignalRejected) {
      throw new QuestionRejected("not_waiting", err.message, { cause: err });
    }
    throw err;
  }
  return structuredClone(item);
}
