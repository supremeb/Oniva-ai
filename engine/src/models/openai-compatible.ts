/**
 * Chat Completions streaming adapter for OpenAI-compatible endpoints
 * (OpenRouter and similar). Plain `fetch` + SSE; no SDK.
 *
 * - The request is `POST {baseURL}/chat/completions` with `stream: true` and
 *   `stream_options.include_usage`; the key goes in `Authorization: Bearer`
 *   for this request only and nothing is kept.
 * - Text and tool-call argument deltas map onto the same events the Anthropic
 *   adapter emits. Tool calls stream per `index`; each index becomes one block.
 * - No thinking blocks: reasoning deltas are ignored and `request.thinking`
 *   is not sent.
 * - Usage: `prompt_tokens` includes cached tokens, so uncached input is
 *   `prompt_tokens - cached - cache_write`.
 * - Prompt caching: for models matched by `cacheControl`, one breakpoint goes on
 *   the last system part and one on the last stable user/assistant text part.
 */
import type { Context } from "../core/context.js";
import type { JsonObject } from "../core/json.js";
import { isContextOverflowMessage } from "./anthropic.js";
import { abortMessage, MessageBuilder, modelError } from "./stream.js";
import type {
  Message,
  ModelError,
  ModelProvider,
  ModelRequest,
  StopReason,
  StreamEvent,
  TextContent,
} from "./types.js";

type FetchLike = (url: string | URL | Request, init?: RequestInit) => Promise<Response>;

export interface OpenAICompatibleProviderOptions {
  /** Registry name, e.g. "openrouter". */
  name: string;
  /** Without a trailing `/chat/completions`, e.g. `https://openrouter.ai/api/v1`. */
  baseURL: string;
  /** Sent on every request (e.g. OpenRouter's `HTTP-Referer` and `X-Title`). */
  headers?: Record<string, string>;
  /** Custom fetch (tests inject recorded SSE here). Default: global fetch. */
  fetch?: FetchLike;
  /** Which models take `cache_control` breakpoints. Default: none. */
  cacheControl?: (model: string) => boolean;
}

interface TextPart {
  type: "text";
  text: string;
  cache_control?: { type: "ephemeral" };
}

type ChatMessage =
  | { role: "system"; content: string | TextPart[] }
  | { role: "user"; content: string | TextPart[] }
  | {
      role: "assistant";
      content: string | TextPart[] | null;
      tool_calls?: Array<{
        id: string;
        type: "function";
        function: { name: string; arguments: string };
      }>;
    }
  | { role: "tool"; tool_call_id: string; content: string };

export interface ChatCompletionsParams {
  model: string;
  messages: ChatMessage[];
  stream: true;
  stream_options: { include_usage: true };
  max_tokens: number;
  tools?: Array<{
    type: "function";
    function: { name: string; description: string; parameters: JsonObject };
  }>;
}

interface ChunkToolCall {
  index?: number;
  id?: string;
  function?: { name?: string; arguments?: string };
}

interface Chunk {
  choices?: Array<{
    delta?: { content?: string | null; tool_calls?: ChunkToolCall[] };
    finish_reason?: string | null;
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    prompt_tokens_details?: { cached_tokens?: number; cache_write_tokens?: number } | null;
  } | null;
  error?: { message?: string; code?: number | string; type?: string } | null;
}

const EPHEMERAL = { type: "ephemeral" } as const;

export class OpenAICompatibleProvider implements ModelProvider {
  readonly name: string;
  readonly #options: OpenAICompatibleProviderOptions;

  constructor(options: OpenAICompatibleProviderOptions) {
    this.name = options.name;
    this.#options = options;
  }

  /** The request body this provider sends (exposed for inspection and tests). */
  buildParams(request: ModelRequest): ChatCompletionsParams {
    const cache = (request.cache ?? true) && (this.#options.cacheControl?.(request.model) ?? false);
    const params: ChatCompletionsParams = {
      model: request.model,
      messages: mapMessages(request.messages, cache),
      stream: true,
      stream_options: { include_usage: true },
      max_tokens: request.maxTokens,
    };
    if (request.tools && request.tools.length > 0) {
      params.tools = request.tools.map((t) => ({
        type: "function",
        function: { name: t.name, description: t.description, parameters: t.parameters },
      }));
    }
    return params;
  }

  async *stream(ctx: Context, request: ModelRequest): AsyncIterable<StreamEvent> {
    const b = new MessageBuilder(this.name, request.model);
    if (ctx.cancelled) {
      yield b.fail(modelError("aborted", abortMessage(ctx)));
      return;
    }

    let textIndex: number | undefined;
    // Chat Completions tool_call index -> neutral block index.
    const toolIndex = new Map<number, number>();
    const open: number[] = [];
    let finishReason: string | null = null;
    let sawDone = false;

    const closeText = function* (): Generator<StreamEvent> {
      if (textIndex === undefined) return;
      open.splice(open.indexOf(textIndex), 1);
      yield b.endBlock(textIndex);
      textIndex = undefined;
    };

    try {
      const doFetch: FetchLike = this.#options.fetch ?? fetch;
      const res = await doFetch(`${this.#options.baseURL.replace(/\/+$/, "")}/chat/completions`, {
        method: "POST",
        headers: {
          ...this.#options.headers,
          "content-type": "application/json",
          accept: "text/event-stream",
          authorization: `Bearer ${request.apiKey}`,
        },
        body: JSON.stringify(this.buildParams(request)),
        signal: ctx.signal,
      });
      if (!res.ok) {
        yield b.fail(await httpError(res));
        return;
      }
      if (!res.body) {
        yield b.fail(modelError("network", "response had no body"));
        return;
      }
      yield b.start();

      for await (const data of sseData(res.body, ctx)) {
        if (data === "[DONE]") {
          sawDone = true;
          break;
        }
        let chunk: Chunk;
        try {
          chunk = JSON.parse(data) as Chunk;
        } catch {
          continue;
        }
        if (chunk.error) {
          yield b.fail(streamError(chunk.error));
          return;
        }
        const u = chunk.usage;
        if (u) {
          const cached = u.prompt_tokens_details?.cached_tokens ?? 0;
          const written = u.prompt_tokens_details?.cache_write_tokens ?? 0;
          b.setUsage({
            inputTokens: Math.max(0, (u.prompt_tokens ?? 0) - cached - written),
            outputTokens: u.completion_tokens ?? 0,
            cacheReadTokens: cached,
            cacheWriteTokens: written,
          });
        }
        const choice = chunk.choices?.[0];
        if (!choice) continue;
        const delta = choice.delta;
        if (delta?.content) {
          if (textIndex === undefined) {
            const ev = b.startBlock({ type: "text", text: "" });
            textIndex = ev.index;
            open.push(ev.index);
            yield ev;
          }
          yield b.delta(textIndex, { type: "text", text: delta.content });
        }
        for (const tc of delta?.tool_calls ?? []) {
          const key = tc.index ?? 0;
          let idx = toolIndex.get(key);
          if (idx === undefined) {
            yield* closeText();
            const ev = b.startBlock({
              type: "tool_call",
              id: tc.id ?? `call_${key}`,
              name: tc.function?.name ?? "",
              arguments: {},
            });
            idx = ev.index;
            toolIndex.set(key, idx);
            open.push(idx);
            yield ev;
          } else if (tc.function?.name) {
            const name = tc.function.name;
            b.update(idx, (block) => {
              if (block.type === "tool_call" && block.name === "") block.name = name;
            });
          }
          const args = tc.function?.arguments;
          if (args) yield b.delta(idx, { type: "tool_args", partialJson: args });
        }
        if (choice.finish_reason) finishReason = choice.finish_reason;
      }
    } catch (err) {
      yield b.fail(classifyOpenAIError(err, ctx));
      return;
    }

    if (!sawDone && finishReason === null) {
      yield b.fail(
        ctx.cancelled
          ? modelError("aborted", abortMessage(ctx))
          : modelError("network", "stream ended before a finish reason"),
      );
      return;
    }
    if (finishReason === "error") {
      yield b.fail(modelError("provider", "the provider reported an error finish"));
      return;
    }
    for (const idx of [...open]) yield b.endBlock(idx);
    yield b.done(mapFinishReason(finishReason, toolIndex.size > 0));
  }
}

/** Yield the `data:` payload of each SSE event; comment lines (": ...") are skipped. */
async function* sseData(body: ReadableStream<Uint8Array>, ctx: Context): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      if (ctx.cancelled) return;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep = nextBoundary(buffer);
      while (sep) {
        const raw = buffer.slice(0, sep.at);
        buffer = buffer.slice(sep.at + sep.length);
        const data = eventData(raw);
        if (data !== undefined) yield data;
        sep = nextBoundary(buffer);
      }
    }
    buffer += decoder.decode();
    const data = eventData(buffer);
    if (data !== undefined) yield data;
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

function nextBoundary(buffer: string): { at: number; length: number } | undefined {
  const m = /\r\n\r\n|\n\n|\r\r/.exec(buffer);
  return m ? { at: m.index, length: m[0].length } : undefined;
}

function eventData(raw: string): string | undefined {
  const lines: string[] = [];
  for (const line of raw.split(/\r\n|\n|\r/)) {
    if (line.startsWith("data:")) lines.push(line.slice(5).replace(/^ /, ""));
  }
  return lines.length > 0 ? lines.join("\n") : undefined;
}

function mapMessages(messages: Message[], cache: boolean): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const m of messages) {
    if (m.role === "system") {
      out.push({ role: "system", content: m.text });
    } else if (m.role === "user") {
      out.push({ role: "user", content: m.content });
    } else if (m.role === "tool_result") {
      out.push({ role: "tool", tool_call_id: m.toolCallId, content: flatten(m.content) });
    } else {
      let text = "";
      const toolCalls: NonNullable<Extract<ChatMessage, { role: "assistant" }>["tool_calls"]> = [];
      for (const block of m.blocks) {
        if (block.type === "text") text += block.text;
        else if (block.type === "tool_call") {
          toolCalls.push({
            id: block.id,
            type: "function",
            function: { name: block.name, arguments: JSON.stringify(block.arguments) },
          });
        }
      }
      if (text === "" && toolCalls.length === 0) continue;
      const msg: ChatMessage = { role: "assistant", content: text === "" ? null : text };
      if (toolCalls.length > 0) msg.tool_calls = toolCalls;
      out.push(msg);
    }
  }
  if (cache) markCacheBreakpoints(out);
  return out;
}

function flatten(content: string | TextContent[]): string {
  return typeof content === "string" ? content : content.map((c) => c.text).join("");
}

function toParts(content: string | TextContent[]): TextPart[] {
  return typeof content === "string"
    ? [{ type: "text", text: content }]
    : content.map((c) => ({ type: "text", text: c.text }));
}

/** Turn the last system message and the last stable user/assistant message into part arrays with a breakpoint. */
function markCacheBreakpoints(messages: ChatMessage[]): void {
  const mark = (m: ChatMessage): boolean => {
    if (m.role === "tool" || m.content === null) return false;
    const parts = toParts(m.content as string | TextContent[]);
    const last = parts.at(-1);
    if (!last || last.text === "") return false;
    last.cache_control = EPHEMERAL;
    (m as { content: TextPart[] }).content = parts;
    return true;
  };
  const lastSystem = messages.findLast((m) => m.role === "system");
  if (lastSystem) mark(lastSystem);
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as ChatMessage;
    if ((m.role === "user" || m.role === "assistant") && mark(m)) return;
  }
}

function mapFinishReason(reason: string | null, hasToolCalls: boolean): StopReason {
  switch (reason) {
    case "tool_calls":
    case "function_call":
      return "tool_use";
    case "length":
      return "max_tokens";
    case "content_filter":
      return "refusal";
    default:
      return hasToolCalls ? "tool_use" : "end_turn";
  }
}

interface ErrorBody {
  error?: { message?: string; code?: number | string; type?: string } | string;
  message?: string;
}

/** Classify a non-2xx response. */
async function httpError(res: Response): Promise<ModelError> {
  let message = `HTTP ${res.status}`;
  let code: string | undefined;
  try {
    const text = await res.text();
    try {
      const body = JSON.parse(text) as ErrorBody;
      if (typeof body.error === "string") message = body.error;
      else if (body.error?.message) {
        message = body.error.message;
        if (body.error.code !== undefined) code = String(body.error.code);
      } else if (body.message) message = body.message;
    } catch {
      if (text.trim() !== "") message = text.trim().slice(0, 500);
    }
  } catch {
    // keep the status message
  }
  return classifyStatus(res.status, message, code);
}

export function classifyStatus(status: number, message: string, code?: string): ModelError {
  const extra = { status };
  if (
    (status === 400 || status === 413) &&
    (isContextOverflowMessage(message) || code === "context_length_exceeded")
  ) {
    return modelError("context_overflow", message, extra);
  }
  if (status === 401 || status === 403 || status === 402) return modelError("auth", message, extra);
  if (status === 429) return modelError("rate_limited", message, extra);
  if (status === 408) return modelError("network", message, extra);
  if (status >= 500) return modelError("overloaded", message, extra);
  if (status >= 400) return modelError("invalid_request", message, extra);
  return modelError("provider", message, extra);
}

/** An error object delivered inside a 200 stream. */
function streamError(error: NonNullable<Chunk["error"]>): ModelError {
  const message = error.message ?? "provider error";
  const status = typeof error.code === "number" ? error.code : undefined;
  if (isContextOverflowMessage(message) || error.code === "context_length_exceeded") {
    return modelError("context_overflow", message, status === undefined ? {} : { status });
  }
  return status === undefined ? modelError("provider", message) : classifyStatus(status, message);
}

export function classifyOpenAIError(err: unknown, ctx: Context): ModelError {
  if (ctx.cancelled || (err instanceof Error && err.name === "AbortError")) {
    return modelError("aborted", abortMessage(ctx));
  }
  if (
    err instanceof TypeError ||
    (err instanceof Error && /fetch|network|socket|terminated/i.test(err.message))
  ) {
    return modelError("network", err.message);
  }
  return modelError("provider", err instanceof Error ? err.message : String(err));
}

/** OpenRouter: Claude models accept `cache_control` breakpoints. */
export const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";

export function openRouterProvider(
  options: Partial<OpenAICompatibleProviderOptions> = {},
): OpenAICompatibleProvider {
  return new OpenAICompatibleProvider({
    name: "openrouter",
    baseURL: OPENROUTER_BASE_URL,
    cacheControl: (model) => model.startsWith("anthropic/"),
    ...options,
  });
}
