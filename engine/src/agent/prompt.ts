/**
 * Agent configuration and the system prompt.
 *
 * Each conversation stores its agent configuration in the `agent.config`
 * document. The model sees the system prompt and tool set through `system`
 * entries: a generation appends one only when the prompt or the enabled tool
 * set differs from the newest `system` entry (compared by fingerprint), so an
 * unchanged prompt is never re-sent as a new entry.
 */
import { createHash } from "node:crypto";
import { z } from "zod";
import { isJson, type JsonValue } from "../core/json.js";
import type { ToolSpec } from "../models/types.js";
import { defineDoc } from "../session/documents.js";

const THINKING = ["off", "low", "medium", "high", "xhigh", "max"] as const;

export const jsonValue = z.custom<JsonValue>((v) => isJson(v), "not JSON");

const tokens = z.number().int().min(0);

/** Compaction settings (see `compaction.ts`); every field is optional per conversation. */
export const compactionSettingsSchema = z.object({
  /** Off: no compaction, and a context overflow fails the run. */
  enabled: z.boolean(),
  /** The model's context window in tokens; null to look it up in `contextWindows`. */
  contextWindow: z.number().int().positive().nullable(),
  /** Context windows by model id, used when `contextWindow` is null. */
  contextWindows: z.record(z.string(), z.number().int().positive()),
  /** Tokens kept free for the reply: requests wait for a compaction at `window - reserve`. */
  reserveTokens: tokens,
  /** A background compaction starts at `window - reserve - backgroundMargin`. */
  backgroundMargin: tokens,
  /** About this many tokens of recent context stay verbatim after a compaction. */
  keepRecentTokens: tokens,
});

export type CompactionSettings = z.infer<typeof compactionSettingsSchema>;

/** Per-conversation compaction overrides: any subset of the settings. */
export type CompactionOverrides = { [K in keyof CompactionSettings]?: CompactionSettings[K] };

const compactionOverridesSchema = compactionSettingsSchema
  .partial()
  .strict() as unknown as z.ZodType<CompactionOverrides>;

const agentConfigSchema = z.object({
  /** System prompt text. */
  systemPrompt: z.string(),
  /** Names of the tools this conversation enables (must also be registered). */
  tools: z.array(z.string()),
  /** Model hint for `resolveModelRequest`; the policy decides. */
  model: z.string().nullable(),
  thinking: z.enum(THINKING).nullable(),
  /** Subagent nesting depth (0 for a top-level conversation). */
  depth: z.number().int().min(0),
  /** Host data, passed through to policy and hooks. */
  metadata: z.record(z.string(), jsonValue),
  /** Compaction overrides for this conversation (over the harness defaults). */
  compaction: compactionOverridesSchema.nullable().default(null),
});

export type AgentConfig = z.infer<typeof agentConfigSchema>;

/**
 * Validate a partial agent config from an untrusted caller. Unknown fields
 * and wrongly typed values fail; the result holds only the given fields.
 */
export function parseAgentConfigInput(
  value: unknown,
): { ok: true; value: AgentConfigInput } | { ok: false; reason: string } {
  const parsed = agentConfigSchema.partial().strict().safeParse(value);
  if (parsed.success) return { ok: true, value: parsed.data as AgentConfigInput };
  const issue = parsed.error.issues[0];
  const path = issue?.path.join(".") ?? "";
  return {
    ok: false,
    reason: `${path === "" ? "agent" : `agent.${path}`}: ${issue?.message ?? "invalid"}`,
  };
}

/** Fields a caller may give when creating or reconfiguring a conversation. */
export type AgentConfigInput = Partial<AgentConfig>;

export const agentConfigDoc = defineDoc<AgentConfig>({
  kind: "agent.config",
  scope: "conversation",
  initial: {
    systemPrompt: "",
    tools: [],
    model: null,
    thinking: null,
    depth: 0,
    metadata: {},
    compaction: null,
  },
  schema: agentConfigSchema,
});

export function applyAgentConfig(base: AgentConfig, patch: AgentConfigInput): AgentConfig {
  const out: AgentConfig = structuredClone(base);
  for (const [key, value] of Object.entries(patch)) {
    if (value !== undefined) (out as Record<string, unknown>)[key] = structuredClone(value);
  }
  return out;
}

/** Payload of a `system` entry. */
export type SystemPayload = {
  text: string;
  tools: string[];
  fingerprint: string;
};

/** Identifies a prompt + tool set; a change means a new `system` entry. */
export function systemFingerprint(text: string, tools: readonly ToolSpec[]): string {
  return createHash("sha256").update(JSON.stringify({ text, tools })).digest("hex");
}

export function systemPayload(text: string, tools: readonly ToolSpec[]): SystemPayload {
  return { text, tools: tools.map((t) => t.name), fingerprint: systemFingerprint(text, tools) };
}
