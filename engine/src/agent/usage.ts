/**
 * Token and cost totals per conversation (`agent.usage` document), overall,
 * per `provider/model` and per purpose (`generation`, `compaction`). Updated
 * in the same commit as the reply or summary it counts.
 */
import { z } from "zod";
import type { Usage } from "../models/types.js";
import { type DocDraft, defineDoc } from "../session/documents.js";
import type { ModelPurpose } from "./policy.js";

const tallySchema = z.object({
  requests: z.number().int().min(0),
  inputTokens: z.number().min(0),
  outputTokens: z.number().min(0),
  cacheReadTokens: z.number().min(0),
  cacheWriteTokens: z.number().min(0),
  /** USD, summed over requests whose model the host prices; null when none were priced. */
  costUsd: z.number().nullable(),
});

export type UsageTally = z.infer<typeof tallySchema>;

const usageSchema = z.object({
  requests: z.number().int().min(0),
  total: tallySchema,
  byModel: z.record(z.string(), tallySchema),
  /** Documents written before purposes were tracked read as empty. */
  byPurpose: z.record(z.string(), tallySchema).default({}),
});

export type UsageTotals = z.infer<typeof usageSchema>;

const emptyTally = (): UsageTally => ({
  requests: 0,
  inputTokens: 0,
  outputTokens: 0,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  costUsd: null,
});

export const usageDoc = defineDoc<UsageTotals>({
  kind: "agent.usage",
  scope: "conversation",
  initial: { requests: 0, total: emptyTally(), byModel: {}, byPurpose: {} },
  schema: usageSchema,
});

function add(t: UsageTally, u: Usage): UsageTally {
  return {
    requests: t.requests + 1,
    inputTokens: t.inputTokens + u.inputTokens,
    outputTokens: t.outputTokens + u.outputTokens,
    cacheReadTokens: t.cacheReadTokens + u.cacheReadTokens,
    cacheWriteTokens: t.cacheWriteTokens + u.cacheWriteTokens,
    costUsd: u.cost ? (t.costUsd ?? 0) + u.cost.total : t.costUsd,
  };
}

/** Count one model request's usage into a draft of the usage document. */
export function recordUsage(
  draft: DocDraft<UsageTotals>,
  provider: string,
  model: string,
  usage: Usage,
  purpose: ModelPurpose = "generation",
): void {
  draft.update((d) => {
    const key = provider ? `${provider}/${model}` : model || "unknown";
    d.requests += 1;
    d.total = add(d.total, usage);
    d.byModel[key] = add(d.byModel[key] ?? emptyTally(), usage);
    d.byPurpose[purpose] = add(d.byPurpose[purpose] ?? emptyTally(), usage);
  });
}
