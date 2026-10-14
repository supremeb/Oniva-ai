/**
 * Provider-neutral model types (R1). Every value here is plain JSON so
 * messages, partial output and usage can be committed as-is.
 */
import type { Context } from "../core/context.js";
import type { JsonObject } from "../core/json.js";

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

export interface TextContent {
  type: "text";
  text: string;
}

export interface TextBlock {
  type: "text";
  text: string;
}

/**
 * Model reasoning. `signature` (and `redacted` + `data`) are opaque provider
 * values needed to replay the block to the same provider and model; other
 * providers or models drop thinking blocks on replay.
 */
export interface ThinkingBlock {
  type: "thinking";
  thinking: string;
  signature?: string;
  redacted?: boolean;
  data?: string;
}

export interface ToolCallBlock {
  type: "tool_call";
  id: string;
  name: string;
  /** Parsed arguments; `{}` while streaming or when the raw text was not a JSON object. */
  arguments: JsonObject;
  /** Set when the streamed argument text was not a JSON object; holds the raw text. */
  invalidArguments?: string;
}

export type AssistantBlock = TextBlock | ThinkingBlock | ToolCallBlock;

export interface SystemMessage {
  role: "system";
  text: string;
}

export interface UserMessage {
  role: "user";
  content: string | TextContent[];
}

export interface AssistantMessage {
  role: "assistant";
  /** Provider that produced this message (registry name, e.g. "anthropic"). */
  provider: string;
  /** Exact model id that produced this message. */
  model: string;
  blocks: AssistantBlock[];
  stopReason?: StopReason;
}

export interface ToolResultMessage {
  role: "tool_result";
  toolCallId: string;
  toolName: string;
  content: string | TextContent[];
  isError: boolean;
}

export type Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage;

// ---------------------------------------------------------------------------
// Tools, request, usage
// ---------------------------------------------------------------------------

export interface ToolSpec {
  name: string;
  description: string;
  /** JSON Schema for the arguments object. */
  parameters: JsonObject;
}

/** Reasoning effort. "off" asks for the least reasoning the model allows. */
export type ThinkingLevel = "off" | "low" | "medium" | "high" | "xhigh" | "max";

/**
 * One model request. The key is used for this request only; providers must not
 * keep it after the stream ends.
 */
export interface ModelRequest {
  model: string;
  apiKey: string;
  maxTokens: number;
  messages: Message[];
  tools?: ToolSpec[];
  /** Omitted: the provider/model default. */
  thinking?: ThinkingLevel;
  /** Prompt caching hints (default true). */
  cache?: boolean;
}

/** A request addressed to a registered provider. */
export interface ModelCall extends ModelRequest {
  provider: string;
}

export type StopReason =
  | "end_turn"
  | "max_tokens"
  | "tool_use"
  | "stop_sequence"
  | "refusal"
  | "pause";

export interface UsageCost {
  /** USD. */
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  total: number;
}

export interface Usage {
  /** Uncached input tokens. */
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  /** Present when the host pricing table covers the model. */
  cost?: UsageCost;
}

// ---------------------------------------------------------------------------
// Errors (always delivered as events, never thrown)
// ---------------------------------------------------------------------------

export type ModelErrorKind =
  /** The caller's context was cancelled. */
  | "aborted"
  /** The prompt does not fit the model's context window (compaction can help). */
  | "context_overflow"
  /** `resolveModelRequest` denied the request; nothing was sent. */
  | "denied"
  | "auth"
  | "rate_limited"
  | "overloaded"
  | "invalid_request"
  | "network"
  /** Any other provider failure, including an unknown provider. */
  | "provider";

export interface ModelError {
  kind: ModelErrorKind;
  message: string;
  retryable: boolean;
  /** HTTP status when the provider returned one. */
  status?: number;
  /** Host-defined denial code (kind "denied" only). */
  code?: string;
}

// ---------------------------------------------------------------------------
// Stream events
// ---------------------------------------------------------------------------

export interface StartEvent {
  type: "start";
  provider: string;
  model: string;
}

export interface BlockStartEvent {
  type: "block_start";
  index: number;
  block: AssistantBlock;
}

export type BlockDelta =
  | { type: "text"; text: string }
  | { type: "thinking"; thinking: string }
  | { type: "tool_args"; partialJson: string };

export interface BlockDeltaEvent {
  type: "block_delta";
  index: number;
  delta: BlockDelta;
}

export interface BlockEndEvent {
  type: "block_end";
  index: number;
  /** The completed block. */
  block: AssistantBlock;
}

export interface DoneEvent {
  type: "done";
  message: AssistantMessage;
  usage: Usage;
  stopReason: StopReason;
}

export interface ErrorEvent {
  type: "error";
  error: ModelError;
  /** Everything received before the failure (open blocks included as they stood). */
  partial: AssistantMessage;
  usage: Usage;
}

export type StreamEvent =
  | StartEvent
  | BlockStartEvent
  | BlockDeltaEvent
  | BlockEndEvent
  | DoneEvent
  | ErrorEvent;

export type TerminalEvent = DoneEvent | ErrorEvent;

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

/**
 * A model backend. `stream` yields `start`, block events, then exactly one
 * terminal event. Implementations should report failures as `error` events;
 * the registry converts anything thrown into one as a safety net.
 */
export interface ModelProvider {
  readonly name: string;
  stream(ctx: Context, request: ModelRequest): AsyncIterable<StreamEvent>;
}

/** Outcome of the host's `resolveModelRequest`. */
export type ModelResolution =
  | {
      ok: true;
      provider: string;
      model: string;
      apiKey: string;
      maxTokens: number;
      thinking?: ThinkingLevel;
    }
  | { ok: false; reason: string; code?: string };

/** The non-policy part of a request: what to send, not where or with which key. */
export interface ModelInput {
  messages: Message[];
  tools?: ToolSpec[];
  thinking?: ThinkingLevel;
  cache?: boolean;
}
