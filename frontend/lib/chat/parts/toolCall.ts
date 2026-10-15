import type { MessagePart } from '@/lib/models';
import { compact } from './types';

type ToolCallPart = Extract<MessagePart, { type: 'tool_call' }>;

export type ToolCallFields = Omit<ToolCallPart, 'type'>;

/** A tool_call part from its fields (undefined fields are left out). */
export function buildToolCallPart(fields: ToolCallFields): MessagePart {
  return compact({ type: 'tool_call' as const, ...fields });
}

/** A tool call that has just started: running, with an optional label as the first step. */
export function buildRunningToolCallPart(opts: {
  id: string;
  toolName: string;
  args?: Record<string, unknown>;
  label?: string;
}): MessagePart {
  return buildToolCallPart({
    id: opts.id,
    toolName: opts.toolName,
    status: 'running',
    args: opts.args,
    steps: opts.label ? [{ id: `step-${opts.id}`, label: opts.label, status: 'active' }] : undefined,
  });
}
