/**
 * Type-safe SSE event contracts for chat streaming.
 *
 * Discriminated union ensures exhaustive handling in switch statements
 * and eliminates `any` after JSON.parse.
 */

export type ChatSSEEvent =
  | { type: 'text_delta'; delta: string; partIndex?: number }
  | { type: 'content'; delta: string }
  | { type: 'thinking_delta'; delta: string }
  | { type: 'reasoning'; content: string }
  | { type: 'thinking'; message: string; stage?: string }
  | { type: 'info_message'; message: string; title?: string }
  | { type: 'tool_call_start'; tool_name: string; label?: string; message?: string; args?: Record<string, unknown>; producesFile?: boolean }
  | { type: 'tool_call_start'; toolName: string; toolCallId: string; label?: string; message?: string; args?: Record<string, unknown>; producesFile?: boolean }
  | { type: 'tool_call_progress'; tool_name: string; label?: string; message?: string; progress?: string }
  | { type: 'tool_call_progress'; toolName: string; toolCallId: string; label?: string; message?: string }
  | { type: 'tool_call_complete'; tool: string; success: boolean; result?: unknown; error?: string }
  | { type: 'tool_call_complete'; toolCallId: string; toolName?: string; status: string; result?: unknown; error?: string; durationMs?: number }
  | { type: 'tool_call_result'; tool: string; success: boolean; result?: unknown; error?: string }
  | { type: 'source'; title: string; url: string; snippet?: string; toolCallId?: string }
  | { type: 'part'; part: Record<string, unknown> }
  | { type: 'error'; message: string }
  | { type: 'done'; id?: string; messageId?: string; model?: string; sessionId?: string; usage?: { input_tokens: number; output_tokens: number } }
  | { type: 'workflow_preview'; status: string; workflow_id: string; workflow_name: string; steps: Array<{ node_id: string; label: string; type: string }>; has_hitl_nodes: boolean; hitl_warning?: string | null; input_summary: Record<string, unknown> }
  | { type: 'workflow_progress'; workflowId: string; stepId: string; label: string; status: string; message?: string; elapsedMs?: number }
  | { type: 'workflow_complete'; workflowId: string; workflowName: string; results: Record<string, unknown>; executedNodes: number; totalNodes: number; elapsedMs?: number };

export function parseChatSSEData(raw: string): ChatSSEEvent | null {
  try {
    return JSON.parse(raw) as ChatSSEEvent;
  } catch {
    return null;
  }
}
