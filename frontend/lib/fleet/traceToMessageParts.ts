/**
 * traceToMessageParts — Converts ExecutionTraceStep[] to MessagePart[] + ToolCallMeta
 * so that ChainOfThought can render Fleet runs with the same fidelity as Chat.
 */

import type { ExecutionTraceStep } from '@/lib/types/chat-panels';
import type { MessagePart } from '@/lib/models';
import type { ToolCallMeta } from '@/hooks/useStreamingParts';
import { buildTextPart, buildThinkingPart, buildToolCallPart } from '@/lib/chat/parts';

/** Map trace step status → MessagePart tool_call status */
function mapToolStatus(status: ExecutionTraceStep['status']): 'pending' | 'running' | 'success' | 'error' {
  switch (status) {
    case 'completed': return 'success';
    case 'failed': return 'error';
    case 'running': return 'running';
    case 'pending': return 'pending';
    case 'skipped': return 'success'; // treat as done
    default: return 'pending';
  }
}

export interface TraceConversion {
  parts: MessagePart[];
  toolCallMeta: Map<string, ToolCallMeta>;
}

/**
 * Convert execution trace steps into the MessagePart[] format consumed by ChainOfThought.
 *
 * Mapping:
 *   tool step   → tool_call part  (+ preceding thinking part if step.reasoning exists)
 *   llm step    → thinking part
 *   start step  → text part
 *   other       → text part
 */
export function traceToMessageParts(steps: ExecutionTraceStep[]): TraceConversion {
  const parts: MessagePart[] = [];
  const toolCallMeta = new Map<string, ToolCallMeta>();

  for (const step of steps) {
    // Detect background agent executor format (has `type` = "tool_call" | "text_response")
    const bgType = (step as unknown as Record<string, unknown>).type as string | undefined;
    if (bgType === 'tool_call' || bgType === 'text_response') {
      const raw = step as unknown as Record<string, unknown>;
      const id = `trace-${parts.length}`;

      if (bgType === 'tool_call') {
        const toolName = (raw.tool_name as string) || 'tool';
        const resultPreview = raw.tool_result_preview as string | undefined;
        // Parse result to detect success/error
        let resultObj: unknown = resultPreview;
        let errorMsg: string | undefined;
        try {
          if (resultPreview) {
            const parsed = JSON.parse(resultPreview);
            resultObj = parsed;
            if (parsed?.success === false) errorMsg = parsed.error;
          }
        } catch { /* use raw string */ }

        const status = errorMsg ? 'error' as const : 'success' as const;

        parts.push(buildToolCallPart({
          id,
          toolName,
          status,
          args: raw.tool_args as Record<string, unknown> | undefined,
          result: resultObj,
          error: errorMsg,
          durationMs: raw.duration_ms as number | undefined,
        }));

        toolCallMeta.set(id, {
          id,
          toolName,
          status,
          args: raw.tool_args as Record<string, unknown> | undefined,
          result: resultObj,
          error: errorMsg,
          durationMs: raw.duration_ms as number | undefined,
          sources: [],
        });
      } else {
        // text_response — agent's final or intermediate text
        const content = (raw.content_preview as string) || '';
        if (content) {
          parts.push(buildTextPart(content));
        }
      }
      continue;
    }

    // Original workflow executor format
    const id = step.step_id || `trace-${parts.length}`;
    const nodeType = step.node_type || 'unknown';

    if (nodeType === 'tool') {
      // If tool has reasoning, emit a thinking part first
      if (step.reasoning) {
        parts.push(buildThinkingPart({ content: step.reasoning, isThinking: false }));
      }

      const toolName = step.node_id || 'tool';
      const status = mapToolStatus(step.status);

      parts.push(buildToolCallPart({
        id,
        toolName,
        status,
        args: step.input,
        result: step.output,
        error: step.error,
        durationMs: step.duration_ms,
      }));

      toolCallMeta.set(id, {
        id,
        toolName,
        status,
        args: step.input,
        result: step.output,
        error: step.error,
        durationMs: step.duration_ms,
        label: step.topic || step.search_query,
        description: step.reasoning,
        sources: [],
      });
    } else if (nodeType === 'llm') {
      parts.push(buildThinkingPart({
        content: step.reasoning || 'Thinking...',
        isThinking: step.status === 'running',
      }));
    } else if (nodeType === 'start') {
      parts.push(buildTextPart(step.reasoning || 'Agent started'));
    } else {
      // Generic step — only emit if there's meaningful content
      if (step.reasoning || step.output) {
        parts.push(buildTextPart(step.reasoning || JSON.stringify(step.output)));
      }
    }
  }

  return { parts, toolCallMeta };
}
