import type { SourceItem } from '@/lib/models';

/** Enriched tool call metadata exposed to chat components (Timeline, ChainOfThought). */
export interface ToolCallMeta {
  id: string;
  toolName: string;
  label?: string;
  description?: string;
  status: 'pending' | 'running' | 'success' | 'error';
  args?: Record<string, unknown>;
  result?: unknown;
  error?: string;
  durationMs?: number;
  sources: SourceItem[];
  producesFile?: boolean;
}

/** Drop keys whose value is undefined so built parts stay minimal and JSON-stable. */
export function compact<T extends object>(obj: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined) out[k] = v;
  }
  return out as T;
}
