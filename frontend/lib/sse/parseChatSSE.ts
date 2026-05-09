/**
 * Extracted SSE parser for chat streaming.
 *
 * Replaces the 170-line inline while(true) loop in ChatInterface.tsx
 * with a testable, dependency-injected parser.
 */

import type { SourceItem } from '@/lib/models';
import { parseChatSSEData } from '@/lib/types/sse-events';

export interface SSEEventHandlers {
  onContentDelta: (delta: string) => void;
  onReasoningDelta: (content: string) => void;
  onThinkingDelta: (delta: string) => void;
  onToolCallStart: (toolName: string, toolCallId?: string, label?: string, message?: string, args?: Record<string, unknown>, producesFile?: boolean) => void;
  onToolCallProgress: (toolName: string, label?: string, message?: string) => void;
  onToolCallComplete: (result: { toolCallId?: string; tool: string; success: boolean; result?: unknown; error?: string; durationMs?: number }) => void;
  onSource: (source: SourceItem & { toolCallId?: string }) => void;
  onInfoMessage: (message: string, title?: string) => void;
  onPart: (part: Record<string, unknown>) => void;
  onDeepWorkSuggested?: (reasoning: string) => void;
  onWorkflowPreview?: (data: { status: string; workflow_id: string; workflow_name: string; steps: Array<{ node_id: string; label: string; type: string }>; has_hitl_nodes: boolean; hitl_warning?: string | null; input_summary: Record<string, unknown> }) => void;
  onWorkflowProgress?: (data: { workflowId: string; stepId: string; label: string; status: string; message?: string; elapsedMs?: number }) => void;
  onWorkflowComplete?: (data: { workflowId: string; workflowName: string; results: Record<string, unknown>; executedNodes: number; totalNodes: number; elapsedMs?: number }) => void;
  onError: (message: string) => void;
  onDone: (data: { id?: string; model?: string; sessionId?: string; usage?: { input_tokens: number; output_tokens: number } }) => void;
}

/**
 * Infer event type from data shape when the SSE `event:` line is missing.
 * This is a safety net — under normal operation, every event has an `event:` line.
 */
function inferEventType(data: Record<string, unknown>): string | undefined {
  if ('delta' in data && data.delta) return 'text_delta';
  if ('content' in data && typeof data.content === 'string') return 'reasoning';
  if ('toolCallId' in data || 'tool_call_id' in data) {
    if ('status' in data) return 'tool_call_complete';
    if ('toolName' in data || 'tool_name' in data) return 'tool_call_start';
  }
  if ('message' in data && 'title' in data) return 'info_message';
  if ('part' in data) return 'part';
  if ('url' in data && 'title' in data) return 'source';
  if ('sessionId' in data || 'messageId' in data || 'model' in data) return 'done';
  return undefined;
}

/**
 * Parse an SSE stream from a ReadableStreamDefaultReader and dispatch events
 * to the provided handler callbacks.
 */
export async function parseChatSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  handlers: SSEEventHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const decoder = new TextDecoder();
  let buffer = '';
  // Persists across blank lines — each `event:` line overwrites it.
  // NOT reset on blank lines because sse_starlette ping comments
  // (`: ping\n\n`) produce blank lines that would erase the type
  // before the subsequent `data:` line is processed.
  let currentEventType: string | null = null;

  while (true) {
    if (signal?.aborted) break;

    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmedLine = line.trim();

      // Empty line = SSE event boundary. Do NOT reset currentEventType
      // here — the next `event:` line will set it. Resetting on blank
      // lines causes event type loss when ping comments interleave.
      if (!trimmedLine) continue;

      // Skip SSE comments/pings
      if (trimmedLine.startsWith(':')) continue;

      // Parse event type
      if (trimmedLine.startsWith('event:')) {
        currentEventType = trimmedLine.slice(6).trim();
        continue;
      }

      // Skip id lines
      if (trimmedLine.startsWith('id:')) continue;

      // Parse data lines
      if (!trimmedLine.startsWith('data:')) continue;

      const jsonStr = trimmedLine.slice(5).trim();
      if (!jsonStr) continue;

      const data = parseChatSSEData(jsonStr);
      if (!data) continue;

      // Dispatch based on SSE event type (from `event:` line), data.type, or shape inference
      const eventType = currentEventType || data.type || inferEventType(data as Record<string, unknown>);

      switch (eventType) {
        case 'content':
        case 'text_delta':
          if ('delta' in data && data.delta) {
            handlers.onContentDelta(data.delta);
          }
          break;

        case 'reasoning':
          if ('content' in data && data.content) {
            handlers.onReasoningDelta(data.content);
          }
          break;

        case 'thinking':
        case 'thinking_delta':
          if ('delta' in data && data.delta) {
            handlers.onThinkingDelta(data.delta);
          } else if ('message' in data && data.message) {
            handlers.onThinkingDelta(data.message);
          }
          break;

        case 'info_message':
          if ('message' in data) {
            handlers.onInfoMessage(data.message, 'title' in data ? data.title : undefined);
          }
          break;

        case 'tool_call_start':
          if ('tool_name' in data || 'toolName' in data) {
            const toolName = ('tool_name' in data ? data.tool_name : (data as Record<string, unknown>).toolName) as string;
            const toolCallId = ('toolCallId' in data ? data.toolCallId : ('tool_call_id' in data ? data.tool_call_id : undefined)) as string | undefined;
            const args = ('args' in data ? data.args : undefined) as Record<string, unknown> | undefined;
            const producesFile = ('producesFile' in data ? !!data.producesFile : false);
            handlers.onToolCallStart(toolName, toolCallId, data.label, data.message, args, producesFile);
          }
          break;

        case 'tool_call_progress':
          if ('tool_name' in data || 'toolName' in data) {
            const toolName = ('tool_name' in data ? data.tool_name : (data as Record<string, unknown>).toolName) as string;
            handlers.onToolCallProgress(toolName, data.label, data.message);
          }
          break;

        case 'tool_call_complete':
        case 'tool_call_result': {
          const d = data as Record<string, unknown>;
          const toolCallId = (d.toolCallId ?? d.tool_call_id) as string | undefined;
          const tool = (d.tool ?? d.toolName ?? 'unknown') as string;
          // Backend sends status:"success"/"error"; legacy clients may send boolean success
          const success = 'status' in d
            ? d.status === 'success'
            : ('success' in d ? !!d.success : !('error' in d && d.error));
          const durationMs = ('durationMs' in d ? d.durationMs : undefined) as number | undefined;
          if (toolCallId || tool !== 'unknown') {
            handlers.onToolCallComplete({ toolCallId, tool, success, result: d.result, error: d.error as string | undefined, durationMs });
          }
          break;
        }

        case 'source':
          if ('url' in data && 'title' in data) {
            handlers.onSource({
              title: data.title,
              url: data.url,
              snippet: 'snippet' in data ? data.snippet : undefined,
              sourceType: ('sourceType' in data ? data.sourceType : undefined) as SourceItem['sourceType'],
              toolCallId: ('toolCallId' in data ? data.toolCallId : undefined) as string | undefined,
            });
          }
          break;

        case 'part':
          if ('part' in data && data.part) {
            handlers.onPart(data.part as Record<string, unknown>);
          }
          break;

        case 'deep_work_suggested':
          if ('reasoning' in data) {
            handlers.onDeepWorkSuggested?.(data.reasoning as string);
          }
          break;

        case 'workflow_preview':
          handlers.onWorkflowPreview?.(data as any);
          break;

        case 'workflow_progress':
          handlers.onWorkflowProgress?.(data as any);
          break;

        case 'workflow_complete':
          handlers.onWorkflowComplete?.(data as any);
          break;

        case 'error':
          if ('message' in data) {
            handlers.onError(data.message);
          }
          break;

        case 'done':
          handlers.onDone(data as { id?: string; model?: string; sessionId?: string });
          break;

        default:
          // Fallback: if delta exists, treat as content
          if ('delta' in data && data.delta) {
            handlers.onContentDelta(data.delta);
          }
      }
    }
  }
}
