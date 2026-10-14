/**
 * Scripted model provider for tests. Each request consumes the next queued
 * response (or calls it, when the response is a function of the request).
 * Delays use the context's injected clock, so a ManualClock drives them.
 */
import type { Context } from "../core/context.js";
import type { JsonObject } from "../core/json.js";
import { abortMessage, MessageBuilder, modelError } from "./stream.js";
import type {
  Message,
  ModelErrorKind,
  ModelProvider,
  ModelRequest,
  StopReason,
  StreamEvent,
  Usage,
} from "./types.js";

export type FakeStep =
  /** Text block; `chunkSize` splits it into several deltas. */
  | { type: "text"; text: string; chunkSize?: number }
  | { type: "thinking"; thinking: string; chunkSize?: number }
  | { type: "tool_call"; id?: string; name: string; arguments: JsonObject }
  /** Wait on the injected clock (abortable). */
  | { type: "delay"; ms: number }
  /** Fail here; blocks emitted so far become the partial message. */
  | { type: "error"; kind: ModelErrorKind; message?: string; status?: number };

export interface FakeResponse {
  steps: FakeStep[];
  /** Default: "tool_use" when a tool call was emitted, else "end_turn". */
  stopReason?: StopReason;
  /** Default: a 4-characters-per-token estimate of request and reply. */
  usage?: Partial<Usage>;
  /** Clock delay before every delta. */
  chunkDelayMs?: number;
}

export type FakeScript = FakeResponse | ((request: ModelRequest) => FakeResponse);

export interface FakeProviderOptions {
  name?: string;
  responses?: FakeScript[];
}

export function fakeText(text: string, extra: Omit<FakeResponse, "steps"> = {}): FakeResponse {
  return { ...extra, steps: [{ type: "text", text }] };
}

export function fakeToolCall(
  name: string,
  args: JsonObject,
  extra: { id?: string; text?: string } = {},
): FakeResponse {
  const steps: FakeStep[] = [];
  if (extra.text !== undefined) steps.push({ type: "text", text: extra.text });
  const call: FakeStep = { type: "tool_call", name, arguments: args };
  if (extra.id !== undefined) call.id = extra.id;
  steps.push(call);
  return { steps };
}

export function fakeError(kind: ModelErrorKind, message = `fake ${kind}`): FakeResponse {
  return { steps: [{ type: "error", kind, message }] };
}

export function fakeContextOverflow(): FakeResponse {
  return fakeError("context_overflow", "prompt is too long for the fake context window");
}

export class FakeProvider implements ModelProvider {
  readonly name: string;
  /** Every request received, in order (includes the key, for assertions). */
  readonly requests: ModelRequest[] = [];
  readonly #queue: FakeScript[];
  #callSeq = 0;

  constructor(options: FakeProviderOptions = {}) {
    this.name = options.name ?? "fake";
    this.#queue = [...(options.responses ?? [])];
  }

  enqueue(...responses: FakeScript[]): this {
    this.#queue.push(...responses);
    return this;
  }

  pending(): number {
    return this.#queue.length;
  }

  async *stream(ctx: Context, request: ModelRequest): AsyncIterable<StreamEvent> {
    this.requests.push(structuredClone(request));
    const b = new MessageBuilder(this.name, request.model);
    const script = this.#queue.shift();
    if (!script) {
      yield b.fail(modelError("provider", "fake provider script exhausted", { retryable: false }));
      return;
    }
    const response = typeof script === "function" ? script(request) : script;
    const pause = async (ms: number): Promise<boolean> => {
      if (ms <= 0) return !ctx.cancelled;
      try {
        await ctx.clock.sleep(ms, ctx.signal);
        return true;
      } catch {
        return false;
      }
    };
    const aborted = () => b.fail(modelError("aborted", abortMessage(ctx)));

    if (ctx.cancelled) {
      yield aborted();
      return;
    }
    yield b.start();
    let outChars = 0;
    let sawToolCall = false;
    const delay = response.chunkDelayMs ?? 0;

    for (const step of response.steps) {
      if (ctx.cancelled) {
        yield aborted();
        return;
      }
      switch (step.type) {
        case "delay":
          if (!(await pause(step.ms))) {
            yield aborted();
            return;
          }
          break;
        case "error": {
          const extra = step.status === undefined ? {} : { status: step.status };
          yield b.fail(modelError(step.kind, step.message ?? `fake ${step.kind}`, extra));
          return;
        }
        case "text":
        case "thinking": {
          const body = step.type === "text" ? step.text : step.thinking;
          const s = b.startBlock(
            step.type === "text" ? { type: "text", text: "" } : { type: "thinking", thinking: "" },
          );
          yield s;
          for (const piece of chunks(body, step.chunkSize)) {
            if (!(await pause(delay))) {
              yield aborted();
              return;
            }
            yield b.delta(
              s.index,
              step.type === "text"
                ? { type: "text", text: piece }
                : { type: "thinking", thinking: piece },
            );
          }
          yield b.endBlock(s.index);
          outChars += body.length;
          break;
        }
        case "tool_call": {
          sawToolCall = true;
          const id = step.id ?? `fake_call_${++this.#callSeq}`;
          const s = b.startBlock({ type: "tool_call", id, name: step.name, arguments: {} });
          yield s;
          const json = JSON.stringify(step.arguments);
          if (!(await pause(delay))) {
            yield aborted();
            return;
          }
          yield b.delta(s.index, { type: "tool_args", partialJson: json });
          yield b.endBlock(s.index);
          outChars += step.name.length + json.length;
          break;
        }
      }
    }

    b.setUsage({
      inputTokens: estimateTokens(messageChars(request.messages)),
      outputTokens: Math.max(1, estimateTokens(outChars)),
      ...response.usage,
    });
    yield b.done(response.stopReason ?? (sawToolCall ? "tool_use" : "end_turn"));
  }
}

function chunks(text: string, size: number | undefined): string[] {
  if (!size || size <= 0 || text.length <= size) return [text];
  const out: string[] = [];
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size));
  return out;
}

function estimateTokens(chars: number): number {
  return Math.ceil(chars / 4);
}

function messageChars(messages: Message[]): number {
  let n = 0;
  for (const m of messages) {
    switch (m.role) {
      case "system":
        n += m.text.length;
        break;
      case "user":
      case "tool_result":
        n +=
          typeof m.content === "string"
            ? m.content.length
            : m.content.reduce((a, c) => a + c.text.length, 0);
        break;
      case "assistant":
        for (const block of m.blocks) {
          if (block.type === "text") n += block.text.length;
          else if (block.type === "thinking") n += block.thinking.length;
          else n += block.name.length + JSON.stringify(block.arguments).length;
        }
        break;
    }
  }
  return n;
}
