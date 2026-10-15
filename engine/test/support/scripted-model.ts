/**
 * A rule-scripted fake model for worker processes (test-only).
 *
 * A worker that restarts must answer a re-requested generation the same way,
 * so responses are a pure function of the request rather than a queue: the
 * first rule matching the request's last message wins. Rules come from a JSON
 * file re-read on every request, so a test may change them between steps.
 *
 * Tool call ids default to `call_<message count>_<index>`: unique per round
 * of a run (idempotency keys are `runId:toolCallId`) and stable across a
 * re-request of the same round.
 */
import { readFileSync } from "node:fs";
import type { Context } from "../../src/core/context.js";
import { FakeProvider, type FakeResponse, type FakeStep, fakeText } from "../../src/models/fake.js";
import type { ModelProvider, ModelRequest, StreamEvent } from "../../src/models/types.js";

export interface RuleMatch {
  /** Role of the request's last message. */
  role?: "user" | "tool_result";
  /** Substring of the last message's text. */
  includes?: string;
  /** Tool name of a `tool_result` last message. */
  tool?: string;
}

/** A fake step; `repeat` repeats a text step's text. */
export type RuleStep =
  | FakeStep
  | { type: "text"; text: string; chunkSize?: number; repeat?: number };

export interface RuleResponse extends Omit<FakeResponse, "steps"> {
  steps: RuleStep[];
}

export interface ModelRule {
  when?: RuleMatch;
  respond: RuleResponse;
}

export interface ModelScript {
  rules: ModelRule[];
  /** Answer when no rule matches. Default: the text "ok". */
  fallback?: RuleResponse;
}

function lastText(message: ModelRequest["messages"][number] | undefined): string {
  if (!message) return "";
  if (message.role === "system") return message.text;
  if (message.role === "assistant") {
    return message.blocks.map((b) => (b.type === "text" ? b.text : "")).join("");
  }
  return typeof message.content === "string"
    ? message.content
    : message.content.map((c) => c.text).join("");
}

function matches(rule: ModelRule, request: ModelRequest): boolean {
  const when = rule.when ?? {};
  const last = request.messages[request.messages.length - 1];
  if (when.role !== undefined && last?.role !== when.role) return false;
  if (when.tool !== undefined && (last?.role !== "tool_result" || last.toolName !== when.tool)) {
    return false;
  }
  if (when.includes !== undefined && !lastText(last).includes(when.includes)) return false;
  return true;
}

/** The response a script gives to a request. */
export function respond(script: ModelScript, request: ModelRequest): FakeResponse {
  const rule = script.rules.find((r) => matches(r, request));
  const chosen = rule?.respond ?? script.fallback ?? fakeText("ok");
  let index = 0;
  const steps: FakeStep[] = chosen.steps.map((step) => {
    if (step.type === "text" && "repeat" in step && typeof step.repeat === "number") {
      const { repeat, ...rest } = step;
      return { ...rest, text: step.text.repeat(repeat) };
    }
    if (step.type === "tool_call" && step.id === undefined) {
      return { ...step, id: `call_${request.messages.length}_${index++}` };
    }
    return step as FakeStep;
  });
  return { ...chosen, steps };
}

/** A provider answering every request from the script file at `path`. */
export class ScriptedModel implements ModelProvider {
  readonly name: string;
  readonly #path: string;

  constructor(path: string, name = "fake") {
    this.name = name;
    this.#path = path;
  }

  stream(ctx: Context, request: ModelRequest): AsyncIterable<StreamEvent> {
    const script = JSON.parse(readFileSync(this.#path, "utf8")) as ModelScript;
    const provider = new FakeProvider({ name: this.name, responses: [respond(script, request)] });
    return provider.stream(ctx, request);
  }
}
