/**
 * Streaming plumbing shared by every provider: a message builder that folds
 * events into an assistant message, a replayable `ModelStream` that guarantees
 * exactly one terminal event (errors and aborts are events, never throws), and
 * the provider registry with the policy-gated `resolveAndStream` entry point.
 */
import type { Context } from "../core/context.js";
import type { JsonObject } from "../core/json.js";
import type {
  AssistantBlock,
  AssistantMessage,
  BlockDelta,
  BlockDeltaEvent,
  BlockEndEvent,
  BlockStartEvent,
  DoneEvent,
  ErrorEvent,
  ModelCall,
  ModelError,
  ModelErrorKind,
  ModelInput,
  ModelProvider,
  ModelResolution,
  StartEvent,
  StopReason,
  StreamEvent,
  TerminalEvent,
  Usage,
} from "./types.js";
import { emptyUsage, type PricingTable, withCost } from "./usage.js";

const RETRYABLE: ReadonlySet<ModelErrorKind> = new Set([
  "rate_limited",
  "overloaded",
  "network",
  "provider",
]);

/** Build a ModelError with the default retryability for its kind. */
export function modelError(
  kind: ModelErrorKind,
  message: string,
  extra: { status?: number; code?: string; retryable?: boolean } = {},
): ModelError {
  const error: ModelError = { kind, message, retryable: extra.retryable ?? RETRYABLE.has(kind) };
  if (extra.status !== undefined) error.status = extra.status;
  if (extra.code !== undefined) error.code = extra.code;
  return error;
}

export function isContextOverflow(event: StreamEvent): boolean {
  return event.type === "error" && event.error.kind === "context_overflow";
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------------------
// MessageBuilder
// ---------------------------------------------------------------------------

/**
 * Folds stream events into an AssistantMessage. Providers use the producing
 * methods (`start`, `startBlock`, `delta`, `endBlock`, `done`, `fail`), which
 * return the event to yield; consumers can rebuild a message with `apply`.
 */
export class MessageBuilder {
  readonly provider: string;
  readonly model: string;
  #blocks: AssistantBlock[] = [];
  #rawArgs = new Map<number, string>();
  #usage: Usage = emptyUsage();

  constructor(provider: string, model: string) {
    this.provider = provider;
    this.model = model;
  }

  start(): StartEvent {
    return { type: "start", provider: this.provider, model: this.model };
  }

  startBlock(block: AssistantBlock): BlockStartEvent {
    const index = this.#blocks.length;
    this.#blocks.push(structuredClone(block));
    if (block.type === "tool_call") this.#rawArgs.set(index, "");
    return { type: "block_start", index, block: structuredClone(block) };
  }

  delta(index: number, delta: BlockDelta): BlockDeltaEvent {
    this.#applyDelta(index, delta);
    return { type: "block_delta", index, delta: { ...delta } };
  }

  endBlock(index: number): BlockEndEvent {
    const block = this.#block(index);
    if (block.type === "tool_call" && this.#rawArgs.has(index)) {
      const raw = this.#rawArgs.get(index) ?? "";
      this.#rawArgs.delete(index);
      const parsed = parseArguments(raw);
      if (parsed === undefined) {
        block.arguments = {};
        block.invalidArguments = raw;
      } else {
        block.arguments = parsed;
      }
    }
    return { type: "block_end", index, block: structuredClone(block) };
  }

  /** Mutate the last-started block of a given index (e.g. to attach a signature). */
  update(index: number, patch: (block: AssistantBlock) => void): void {
    patch(this.#block(index));
  }

  /** Overwrite the given usage fields (providers report cumulative counts). */
  setUsage(patch: Partial<Usage>): void {
    this.#usage = { ...this.#usage, ...patch };
  }

  get usage(): Usage {
    return { ...this.#usage };
  }

  snapshot(stopReason?: StopReason): AssistantMessage {
    const message: AssistantMessage = {
      role: "assistant",
      provider: this.provider,
      model: this.model,
      blocks: structuredClone(this.#blocks),
    };
    if (stopReason !== undefined) message.stopReason = stopReason;
    return message;
  }

  done(stopReason: StopReason): DoneEvent {
    return { type: "done", message: this.snapshot(stopReason), usage: this.usage, stopReason };
  }

  fail(error: ModelError): ErrorEvent {
    return { type: "error", error, partial: this.snapshot(), usage: this.usage };
  }

  /** Fold an event produced elsewhere into this builder. */
  apply(event: StreamEvent): void {
    switch (event.type) {
      case "start":
        return;
      case "block_start":
        this.#blocks[event.index] = structuredClone(event.block);
        if (event.block.type === "tool_call") this.#rawArgs.set(event.index, "");
        return;
      case "block_delta":
        this.#applyDelta(event.index, event.delta);
        return;
      case "block_end":
        this.#blocks[event.index] = structuredClone(event.block);
        this.#rawArgs.delete(event.index);
        return;
      case "done":
      case "error":
        this.#usage = { ...event.usage };
        return;
    }
  }

  #block(index: number): AssistantBlock {
    const block = this.#blocks[index];
    if (!block) throw new RangeError(`no block at index ${index}`);
    return block;
  }

  #applyDelta(index: number, delta: BlockDelta): void {
    const block = this.#block(index);
    if (delta.type === "text" && block.type === "text") {
      block.text += delta.text;
    } else if (delta.type === "thinking" && block.type === "thinking") {
      block.thinking += delta.thinking;
    } else if (delta.type === "tool_args" && block.type === "tool_call") {
      this.#rawArgs.set(index, (this.#rawArgs.get(index) ?? "") + delta.partialJson);
    } else {
      throw new TypeError(`${delta.type} delta does not apply to a ${block.type} block`);
    }
  }
}

function parseArguments(raw: string): JsonObject | undefined {
  if (raw.trim() === "") return {};
  try {
    const value: unknown = JSON.parse(raw);
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      return value as JsonObject;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// ModelStream
// ---------------------------------------------------------------------------

type StreamPlan =
  | { provider: string; model: string; events: () => AsyncIterable<StreamEvent> }
  | { provider: string; model: string; error: ModelError };

/**
 * A replayable stream of model events ending in exactly one terminal event.
 * Iterating more than once replays from the start; `result()` resolves with
 * the terminal event (draining the source if nobody else is).
 */
export class ModelStream implements AsyncIterable<StreamEvent> {
  readonly #ctx: Context;
  readonly #pricing: PricingTable | undefined;
  #plan: (() => Promise<StreamPlan>) | undefined;
  readonly #events: StreamEvent[] = [];
  #terminal: TerminalEvent | undefined;
  #wake: Array<() => void> = [];
  #pump: Promise<void> | undefined;

  constructor(ctx: Context, plan: () => Promise<StreamPlan>, pricing?: PricingTable) {
    this.#ctx = ctx;
    this.#plan = plan;
    this.#pricing = pricing;
  }

  async *[Symbol.asyncIterator](): AsyncIterator<StreamEvent> {
    this.#start();
    let i = 0;
    for (;;) {
      while (i < this.#events.length) {
        yield this.#events[i++] as StreamEvent;
      }
      if (this.#terminal) return;
      await new Promise<void>((resolve) => this.#wake.push(resolve));
    }
  }

  async result(): Promise<TerminalEvent> {
    this.#start();
    await this.#pump;
    return this.#terminal as TerminalEvent;
  }

  #start(): void {
    this.#pump ??= this.#run();
  }

  #push(event: StreamEvent): void {
    this.#events.push(event);
    const wake = this.#wake;
    this.#wake = [];
    for (const w of wake) w();
  }

  async #run(): Promise<void> {
    const planFn = this.#plan as () => Promise<StreamPlan>;
    this.#plan = undefined; // drop the request (and its key) once started
    let plan: StreamPlan;
    try {
      plan = await planFn();
    } catch (err) {
      plan = { provider: "", model: "", error: modelError("provider", errorText(err)) };
    }
    const builder = new MessageBuilder(plan.provider, plan.model);
    const finish = (event: TerminalEvent): void => {
      const priced = {
        ...event,
        usage: withCost(event.usage, plan.provider, plan.model, this.#pricing),
      };
      this.#terminal = priced;
      this.#push(priced);
    };

    if ("error" in plan) {
      finish(builder.fail(plan.error));
      return;
    }
    if (this.#ctx.cancelled) {
      finish(builder.fail(modelError("aborted", abortMessage(this.#ctx))));
      return;
    }
    try {
      for await (const event of plan.events()) {
        builder.apply(event);
        if (event.type === "done" || event.type === "error") {
          finish(event);
          return;
        }
        this.#push(event);
      }
      finish(
        this.#ctx.cancelled
          ? builder.fail(modelError("aborted", abortMessage(this.#ctx)))
          : builder.fail(modelError("provider", "model stream ended without a terminal event")),
      );
    } catch (err) {
      finish(
        builder.fail(
          this.#ctx.cancelled
            ? modelError("aborted", abortMessage(this.#ctx))
            : modelError("provider", errorText(err)),
        ),
      );
    }
  }
}

export function abortMessage(ctx: Context): string {
  const reason = ctx.reason;
  return reason === undefined ? "aborted" : `aborted: ${errorText(reason)}`;
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

export interface ModelRegistryOptions {
  /** Host pricing table; usage on terminal events gets a `cost` when the model is listed. */
  pricing?: PricingTable;
}

export type ModelResolver = (ctx: Context) => ModelResolution | Promise<ModelResolution>;

export class ModelRegistry {
  readonly #providers = new Map<string, ModelProvider>();
  readonly #pricing: PricingTable | undefined;

  constructor(options: ModelRegistryOptions = {}) {
    this.#pricing = options.pricing;
  }

  register(provider: ModelProvider): this {
    if (this.#providers.has(provider.name)) {
      throw new Error(`model provider "${provider.name}" is already registered`);
    }
    this.#providers.set(provider.name, provider);
    return this;
  }

  get(name: string): ModelProvider | undefined {
    return this.#providers.get(name);
  }

  names(): string[] {
    return [...this.#providers.keys()];
  }

  /** Stream a fully specified call. Never throws; failures arrive as an `error` event. */
  stream(ctx: Context, call: ModelCall): ModelStream {
    return new ModelStream(ctx, async () => this.#plan(ctx, call), this.#pricing);
  }

  /**
   * Ask the host policy for model, key and admission, then stream. A denial
   * (or a throwing resolver) yields a single `error` event of kind "denied"
   * and no provider is called.
   */
  resolveAndStream(ctx: Context, resolve: ModelResolver, input: ModelInput): ModelStream {
    return new ModelStream(
      ctx,
      async () => {
        let resolution: ModelResolution;
        try {
          resolution = await resolve(ctx);
        } catch (err) {
          return {
            provider: "",
            model: "",
            error: modelError("denied", `model request resolution failed: ${errorText(err)}`, {
              retryable: false,
            }),
          };
        }
        if (!resolution.ok) {
          const extra = resolution.code === undefined ? {} : { code: resolution.code };
          return { provider: "", model: "", error: modelError("denied", resolution.reason, extra) };
        }
        const call: ModelCall = {
          ...input,
          provider: resolution.provider,
          model: resolution.model,
          apiKey: resolution.apiKey,
          maxTokens: resolution.maxTokens,
        };
        if (resolution.thinking !== undefined) call.thinking = resolution.thinking;
        return this.#plan(ctx, call);
      },
      this.#pricing,
    );
  }

  #plan(ctx: Context, call: ModelCall): StreamPlan {
    const provider = this.#providers.get(call.provider);
    if (!provider) {
      return {
        provider: call.provider,
        model: call.model,
        error: modelError("provider", `unknown model provider "${call.provider}"`, {
          retryable: false,
        }),
      };
    }
    const { provider: _name, ...request } = call;
    return {
      provider: call.provider,
      model: call.model,
      events: () => provider.stream(ctx, request),
    };
  }
}
