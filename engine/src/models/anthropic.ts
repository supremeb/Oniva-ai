/**
 * Anthropic Messages API adapter (KTD7). Streams with the official SDK and
 * maps raw SSE events onto the provider-neutral event model.
 *
 * - A client is built per request from the request's key; nothing is kept.
 * - Thinking: current models take adaptive thinking plus `output_config.effort`
 *   (fixed budgets are rejected there); older budget-only models get
 *   `budget_tokens` derived from the effort level.
 * - Prompt caching: one breakpoint on the system prompt, one on the last
 *   stable (user/assistant) message, so each turn reads the previous prefix.
 */

import Anthropic, { APIConnectionError, APIError, APIUserAbortError } from "@anthropic-ai/sdk";
import type { Context } from "../core/context.js";
import type { JsonObject } from "../core/json.js";
import { modelFetch } from "./http.js";
import { abortMessage, MessageBuilder, modelError } from "./stream.js";
import type {
  Message,
  ModelError,
  ModelProvider,
  ModelRequest,
  StopReason,
  StreamEvent,
  TextContent,
  ThinkingLevel,
} from "./types.js";

type ClientOptions = NonNullable<ConstructorParameters<typeof Anthropic>[0]>;

export const DEFAULT_ANTHROPIC_MAX_RETRIES = 3;
export const DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5";
export const DEFAULT_ANTHROPIC_SUMMARY_MODEL = "claude-haiku-5-5";

export interface AnthropicProviderOptions {
  /** Registry name (default "anthropic"). */
  name?: string;
  baseURL?: string;
  /**
   * SDK-level retries for 408/409/429/5xx and connection errors, before any
   * event has been received (the SDK never replays a started stream).
   * Default 3, set explicitly rather than inherited from the SDK.
   */
  maxRetries?: number;
  /** Request timeout in ms. */
  timeoutMs?: number;
  /** Custom fetch (tests inject recorded SSE here). */
  fetch?: ClientOptions["fetch"];
  /** Thinking visibility on adaptive models (default "summarized" so it can stream). */
  thinkingDisplay?: "summarized" | "omitted";
  /** Override detection of models that only support fixed thinking budgets. */
  usesThinkingBudget?: (model: string) => boolean;
}

type Effort = Exclude<ThinkingLevel, "off">;

const BUDGETS: Record<Effort, number> = {
  low: 2048,
  medium: 8192,
  high: 16384,
  xhigh: 32768,
  max: 65536,
};
const MIN_BUDGET = 1024;

/** Older models that take `thinking.budget_tokens` and reject `effort`/adaptive. */
const BUDGET_THINKING_MODEL =
  /^claude-(?:3[-.].*|(?:opus-4|sonnet-4|opus-4-1|opus-4-5|sonnet-4-5|haiku-4-5)(?:[-@]\d{8})?)$/;

export function usesThinkingBudget(model: string): boolean {
  return BUDGET_THINKING_MODEL.test(model);
}

const EPHEMERAL = { type: "ephemeral" } as const;

export class AnthropicProvider implements ModelProvider {
  readonly name: string;
  readonly #options: AnthropicProviderOptions;

  constructor(options: AnthropicProviderOptions = {}) {
    this.name = options.name ?? "anthropic";
    this.#options = options;
  }

  /** The request body this provider sends (exposed for inspection and tests). */
  buildParams(request: ModelRequest): Anthropic.MessageCreateParamsStreaming {
    const cache = request.cache ?? true;
    const { system, messages } = this.#mapMessages(request, cache);
    const params: Anthropic.MessageCreateParamsStreaming = {
      model: request.model,
      max_tokens: request.maxTokens,
      messages,
      stream: true,
    };
    if (system.length > 0) params.system = system;
    if (request.tools && request.tools.length > 0) {
      params.tools = request.tools.map((t) => ({
        name: t.name,
        description: t.description,
        input_schema: t.parameters as Anthropic.Tool.InputSchema,
        eager_input_streaming: true,
      }));
    }
    this.#applyThinking(params, request);
    return params;
  }

  async *stream(ctx: Context, request: ModelRequest): AsyncIterable<StreamEvent> {
    const b = new MessageBuilder(this.name, request.model);
    if (ctx.cancelled) {
      yield b.fail(modelError("aborted", abortMessage(ctx)));
      return;
    }
    const clientOptions: ClientOptions = { apiKey: request.apiKey, authToken: null };
    if (this.#options.baseURL !== undefined) clientOptions.baseURL = this.#options.baseURL;
    clientOptions.maxRetries = this.#options.maxRetries ?? DEFAULT_ANTHROPIC_MAX_RETRIES;
    if (this.#options.timeoutMs !== undefined) clientOptions.timeout = this.#options.timeoutMs;
    clientOptions.fetch = this.#options.fetch ?? (modelFetch as ClientOptions["fetch"]);
    const client = new Anthropic(clientOptions);

    // Anthropic block index -> neutral block index (unsupported blocks are skipped).
    const indexMap = new Map<number, number>();
    const pendingInput = new Map<number, JsonObject>();
    let stopReason: Anthropic.StopReason | null = null;
    let stopped = false;

    try {
      const events = await client.messages.create(this.buildParams(request), {
        signal: ctx.signal,
      });
      for await (const ev of events) {
        switch (ev.type) {
          case "message_start": {
            const u = ev.message.usage;
            b.setUsage({
              inputTokens: u.input_tokens ?? 0,
              outputTokens: u.output_tokens ?? 0,
              cacheReadTokens: u.cache_read_input_tokens ?? 0,
              cacheWriteTokens: u.cache_creation_input_tokens ?? 0,
            });
            yield b.start();
            break;
          }
          case "content_block_start": {
            const cb = ev.content_block;
            let started: StreamEvent | undefined;
            if (cb.type === "text") {
              started = b.startBlock({ type: "text", text: cb.text });
            } else if (cb.type === "thinking") {
              started = b.startBlock(
                cb.signature
                  ? { type: "thinking", thinking: cb.thinking, signature: cb.signature }
                  : { type: "thinking", thinking: cb.thinking },
              );
            } else if (cb.type === "redacted_thinking") {
              started = b.startBlock({
                type: "thinking",
                thinking: "",
                redacted: true,
                data: cb.data,
              });
            } else if (cb.type === "tool_use") {
              started = b.startBlock({
                type: "tool_call",
                id: cb.id,
                name: cb.name,
                arguments: {},
              });
              const input = cb.input as JsonObject | null;
              if (input && Object.keys(input).length > 0) pendingInput.set(ev.index, input);
            }
            if (started?.type === "block_start") {
              indexMap.set(ev.index, started.index);
              yield started;
            }
            break;
          }
          case "content_block_delta": {
            const idx = indexMap.get(ev.index);
            if (idx === undefined) break;
            const d = ev.delta;
            if (d.type === "text_delta") {
              yield b.delta(idx, { type: "text", text: d.text });
            } else if (d.type === "thinking_delta") {
              yield b.delta(idx, { type: "thinking", thinking: d.thinking });
            } else if (d.type === "signature_delta") {
              b.update(idx, (block) => {
                if (block.type === "thinking")
                  block.signature = (block.signature ?? "") + d.signature;
              });
            } else if (d.type === "input_json_delta") {
              if (d.partial_json === "") break;
              pendingInput.delete(ev.index);
              yield b.delta(idx, { type: "tool_args", partialJson: d.partial_json });
            }
            break;
          }
          case "content_block_stop": {
            const idx = indexMap.get(ev.index);
            if (idx === undefined) break;
            const input = pendingInput.get(ev.index);
            if (input) {
              pendingInput.delete(ev.index);
              yield b.delta(idx, { type: "tool_args", partialJson: JSON.stringify(input) });
            }
            yield b.endBlock(idx);
            break;
          }
          case "message_delta": {
            // Cumulative counts; fields may be null or absent.
            const u = ev.usage;
            if (typeof u.output_tokens === "number") b.setUsage({ outputTokens: u.output_tokens });
            if (typeof u.input_tokens === "number") b.setUsage({ inputTokens: u.input_tokens });
            if (typeof u.cache_read_input_tokens === "number") {
              b.setUsage({ cacheReadTokens: u.cache_read_input_tokens });
            }
            if (typeof u.cache_creation_input_tokens === "number") {
              b.setUsage({ cacheWriteTokens: u.cache_creation_input_tokens });
            }
            if (ev.delta.stop_reason !== null) stopReason = ev.delta.stop_reason;
            break;
          }
          case "message_stop":
            stopped = true;
            break;
        }
        if (stopped) break;
      }
    } catch (err) {
      yield b.fail(classifyAnthropicError(err, ctx));
      return;
    }

    if (!stopped) {
      yield b.fail(
        ctx.cancelled
          ? modelError("aborted", abortMessage(ctx))
          : modelError("network", "stream ended before message_stop"),
      );
      return;
    }
    if (stopReason === "model_context_window_exceeded") {
      yield b.fail(
        modelError("context_overflow", "generation stopped at the model context window"),
      );
      return;
    }
    yield b.done(mapStopReason(stopReason));
  }

  #applyThinking(params: Anthropic.MessageCreateParamsStreaming, request: ModelRequest): void {
    const level = request.thinking;
    if (level === undefined) return;
    const budgetOnly = (this.#options.usesThinkingBudget ?? usesThinkingBudget)(request.model);
    if (budgetOnly) {
      if (level === "off") return;
      const budget = Math.min(BUDGETS[level], request.maxTokens - 1);
      if (budget >= MIN_BUDGET) params.thinking = { type: "enabled", budget_tokens: budget };
      return;
    }
    if (level === "off") {
      // Current models cannot all disable thinking; the lowest effort is the portable "off".
      params.output_config = { effort: "low" };
      return;
    }
    params.thinking = { type: "adaptive", display: this.#options.thinkingDisplay ?? "summarized" };
    params.output_config = { effort: level };
  }

  #mapMessages(
    request: ModelRequest,
    cache: boolean,
  ): { system: Anthropic.TextBlockParam[]; messages: Anthropic.MessageParam[] } {
    const system: Anthropic.TextBlockParam[] = [];
    const out: Anthropic.MessageParam[] = [];
    let leading = true;

    const pushUser = (blocks: Anthropic.ContentBlockParam[]): void => {
      const last = out.at(-1);
      if (last?.role === "user" && Array.isArray(last.content)) last.content.push(...blocks);
      else out.push({ role: "user", content: blocks });
    };

    for (const m of request.messages) {
      if (m.role === "system") {
        if (leading) system.push({ type: "text", text: m.text });
        else out.push({ role: "system", content: m.text });
        continue;
      }
      leading = false;
      if (m.role === "user") {
        pushUser(textBlocks(m.content));
      } else if (m.role === "tool_result") {
        pushUser([
          {
            type: "tool_result",
            tool_use_id: m.toolCallId,
            content: typeof m.content === "string" ? m.content : textBlocks(m.content),
            is_error: m.isError,
          },
        ]);
      } else {
        const content = this.#assistantContent(m, request.model);
        if (content.length > 0) out.push({ role: "assistant", content });
      }
    }

    if (cache) {
      const lastSystem = system.at(-1);
      if (lastSystem) lastSystem.cache_control = EPHEMERAL;
      markLastStable(out);
    }
    return { system, messages: out };
  }

  #assistantContent(
    m: Extract<Message, { role: "assistant" }>,
    model: string,
  ): Anthropic.ContentBlockParam[] {
    // Thinking blocks are bound to the producing provider and model.
    const replayThinking = m.provider === this.name && m.model === model;
    const content: Anthropic.ContentBlockParam[] = [];
    for (const block of m.blocks) {
      if (block.type === "text") {
        if (block.text !== "") content.push({ type: "text", text: block.text });
      } else if (block.type === "thinking") {
        if (!replayThinking) continue;
        if (block.redacted) {
          if (block.data !== undefined)
            content.push({ type: "redacted_thinking", data: block.data });
        } else if (block.signature) {
          content.push({ type: "thinking", thinking: block.thinking, signature: block.signature });
        }
      } else {
        content.push({ type: "tool_use", id: block.id, name: block.name, input: block.arguments });
      }
    }
    return content;
  }
}

function textBlocks(content: string | TextContent[]): Anthropic.TextBlockParam[] {
  return typeof content === "string"
    ? [{ type: "text", text: content }]
    : content.map((c) => ({ type: "text", text: c.text }));
}

/** Put a cache breakpoint on the last cacheable block of the last user/assistant message. */
function markLastStable(messages: Anthropic.MessageParam[]): void {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as Anthropic.MessageParam;
    if (m.role === "system" || !Array.isArray(m.content)) continue;
    for (let j = m.content.length - 1; j >= 0; j--) {
      const block = m.content[j] as Anthropic.ContentBlockParam;
      if (block.type === "text" || block.type === "tool_use" || block.type === "tool_result") {
        block.cache_control = EPHEMERAL;
        return;
      }
    }
  }
}

function mapStopReason(reason: Anthropic.StopReason | null): StopReason {
  switch (reason) {
    case "max_tokens":
    case "tool_use":
    case "stop_sequence":
    case "refusal":
      return reason;
    case "pause_turn":
      return "pause";
    default:
      return "end_turn";
  }
}

const OVERFLOW_MESSAGE =
  /prompt is too long|context (window|length|limit)|exceeds? the (maximum )?context|too many (input )?tokens/i;

export function isContextOverflowMessage(message: string): boolean {
  return OVERFLOW_MESSAGE.test(message);
}

interface ErrorBody {
  error?: { type?: string; message?: string };
}

/** Classify an SDK/transport failure into a neutral ModelError. */
export function classifyAnthropicError(err: unknown, ctx: Context): ModelError {
  if (ctx.cancelled || err instanceof APIUserAbortError) {
    return modelError("aborted", abortMessage(ctx));
  }
  if (err instanceof APIConnectionError) {
    return modelError("network", err.message);
  }
  if (err instanceof APIError) {
    const body = (err.error ?? {}) as ErrorBody;
    const type = err.type ?? body.error?.type ?? undefined;
    const message = body.error?.message ?? err.message;
    const status = err.status;
    const extra = status === undefined ? {} : { status };
    if (
      (status === undefined || status === 400 || status === 413) &&
      isContextOverflowMessage(message)
    ) {
      return modelError("context_overflow", message, extra);
    }
    if (status === 401 || status === 403 || type === "authentication_error") {
      return modelError("auth", message, extra);
    }
    if (status === 429 || type === "rate_limit_error") {
      return modelError("rate_limited", message, extra);
    }
    if (status === 529 || type === "overloaded_error") {
      return modelError("overloaded", message, extra);
    }
    if ((status !== undefined && status >= 500) || type === "api_error") {
      return modelError("provider", message, extra);
    }
    if (status !== undefined && status >= 400) {
      return modelError("invalid_request", message, extra);
    }
    return modelError("provider", message, extra);
  }
  return modelError("provider", err instanceof Error ? err.message : String(err));
}
