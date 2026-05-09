/**
 * useStreamingParts — Accumulates SSE events into a MessagePart[] array.
 *
 * Bridges the SSE event stream from parseChatSSEStream() to the rich
 * MessageRenderer by mapping each event type to the corresponding MessagePart.
 *
 * Key design: thinking segments and tool calls are stored in a single ordered
 * `chainPartsRef` array that preserves temporal insertion order so that
 * reasoning text appears interleaved between tool calls in the UI.
 *
 * Content routing (Phase 2 fix):
 *   - Text BEFORE any tool call → response text (textRef)
 *   - Text DURING active tool execution(s) → thinking (accordion)
 *   - Text BETWEEN completed tool calls (narration) → ToolCallMeta.description
 *   - Text AFTER final tool completes → response text (textRef)
 *
 * Usage:
 *   const { richParts, handlers, reset, sources, toolCallMeta } = useStreamingParts();
 *   // Pass handlers to parseChatSSEStream()
 *   // Pass richParts to MessageRenderer
 *   // Pass toolCallMeta to ChainOfThought for Timeline + TimelineEvent
 *   // Call reset() when starting a new message
 */

import { useCallback, useRef, useState } from 'react';
import type { MessagePart, SourceItem, WorkflowPreview, WorkflowProgressEvent, WorkflowCompleteEvent } from '@/lib/models';
import type { SSEEventHandlers } from '@/lib/sse/parseChatSSE';

/**
 * Strip raw LLM function/tool call markup that leaks into text content.
 * Handles multiple formats:
 *   - XML: <function_calls>...<invoke>...</invoke>...</function_calls>
 *   - DSML: < | DSML | function_calls>...< | DSML | invoke>...</ DSML...>
 *   - Broken: andfunction_calls>, invfunction_calls>, partial tags
 *   - JSON tool calls: {"function_call": ...} blocks after function_calls
 */
function stripFunctionCallMarkup(text: string): string {
  if (!text) return text;
  let cleaned = text;

  // 1. Complete XML blocks: <function_calls>...</function_calls> and variants
  cleaned = cleaned.replace(/<\s*\|?\s*(?:DSML\s*\|?\s*)?function_calls\s*>[\s\S]*?<\s*\/?\s*\|?\s*(?:DSML\s*\|?\s*)?(?:inv)?function_calls\s*>/gi, '');

  // 2. Partial/unclosed function_calls block at end of string
  cleaned = cleaned.replace(/<\s*\|?\s*(?:DSML\s*\|?\s*)?function_calls\s*>[\s\S]*$/gi, '');

  // 3. Any tag containing "function_calls" (catches all variants)
  cleaned = cleaned.replace(/<\/?[^>]*function_calls[^>]*>/gi, '');

  // 4. DSML invoke/parameter tags
  cleaned = cleaned.replace(/<\s*\|?\s*(?:DSML\s*\|?\s*)?\/?\s*(?:invoke|parameter)[^>]*>/gi, '');

  // 5. Standard invoke tags (including <functioninvoke> merged variant from DeepSeek)
  cleaned = cleaned.replace(/<\/?(?:function)?(?:invoke|antml:invoke|antml:parameter)[^>]*>/gi, '');

  // 6. Broken fragments: "andfunction_calls>", "invfunction_calls>", "function_cinvoke..."
  cleaned = cleaned.replace(/(?:and|inv)function_calls\s*>/gi, '');
  cleaned = cleaned.replace(/function_c\w*invoke[^>]*>/gi, '');

  // 7. Stray </parameter or "</parameter patterns (with or without closing >)
  cleaned = cleaned.replace(/"?\s*<\/?\s*parameter\b[^>]*>?/gi, '');

  // 8. JSON tool call blocks that follow function_calls pattern
  //    e.g., { "query": "...", "num_results": 5 } appearing as leaked text
  //    Only strip if preceded by function_calls context (within same line fragment)
  cleaned = cleaned.replace(/function_calls>\s*\{[^}]*\}/gi, '');

  return cleaned.trim();
}

/** Internal source with computed citation number */
interface IndexedSource extends SourceItem {
  citationNumber: number;
  toolCallId?: string;
}

/** Enriched tool call metadata exposed to components */
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

interface StreamingPartsState {
  /** Accumulated rich parts for the current streaming message */
  richParts: MessagePart[];
  /** Accumulated sources for the current message */
  sources: SourceItem[];
  /** Accumulated file parts for the sidebar */
  files: MessagePart[];
  /** Flat text content (for persistence alongside richParts) */
  textContent: string;
  /** Flat reasoning content */
  reasoning: string;
  /** Enriched tool call metadata map (id → ToolCallMeta) */
  toolCallMeta: Map<string, ToolCallMeta>;
  /** Workflow preview from agent (Phase 1 — pending approval) */
  workflowPreview: WorkflowPreview | null;
  /** Per-step workflow execution status (Phase 2 — live progress) */
  workflowProgress: Map<string, WorkflowProgressEvent>;
  /** Final workflow execution result */
  workflowResult: WorkflowCompleteEvent | null;
}

export function useStreamingParts() {
  const [state, setState] = useState<StreamingPartsState>({
    richParts: [],
    sources: [],
    files: [],
    textContent: '',
    reasoning: '',
    toolCallMeta: new Map(),
    workflowPreview: null,
    workflowProgress: new Map(),
    workflowResult: null,
  });

  // ── Mutable refs for accumulation (avoid re-renders on every delta) ──

  const textRef = useRef('');
  // Ordered array of thinking segments + tool call parts (temporal order)
  const chainPartsRef = useRef<MessagePart[]>([]);
  // Map<toolCallId, index in chainPartsRef> for O(1) lookup
  const toolCallIndexRef = useRef<Map<string, number>>(new Map());
  // Index of the currently open thinking segment in chainPartsRef (-1 = none)
  const activeThinkingIdxRef = useRef(-1);
  // Whether we've entered the tool-calling phase (any tool_call_start seen)
  const inToolPhaseRef = useRef(false);
  // Length of textRef at the moment the first tool call starts (splits pre/post-tool text)
  const preToolTextLenRef = useRef(0);
  // Number of currently running tool calls (handles parallel tools correctly)
  const runningToolCountRef = useRef(0);
  // ID of the last tool call that completed
  const lastCompletedToolIdRef = useRef<string | null>(null);
  // Narration text keyed by tool call ID (inter-tool content)
  const narrationRef = useRef<Map<string, string>>(new Map());
  const toolCallMetaRef = useRef<Map<string, ToolCallMeta>>(new Map());
  const infoPartsRef = useRef<MessagePart[]>([]);
  const filesRef = useRef<MessagePart[]>([]);
  const sourcesRef = useRef<IndexedSource[]>([]);
  const sourceCountRef = useRef(0);
  const rafIdRef = useRef<number | null>(null);
  // Whether response text is actively receiving deltas (controls cursor visibility)
  const textActiveRef = useRef(false);
  // Workflow execution state refs
  const workflowPreviewRef = useRef<WorkflowPreview | null>(null);
  const workflowProgressRef = useRef<Map<string, WorkflowProgressEvent>>(new Map());
  const workflowResultRef = useRef<WorkflowCompleteEvent | null>(null);

  // ── Build state snapshot from refs (shared by scheduleUpdate & finalize) ──

  const buildStateFromRefs = useCallback((streaming: boolean): StreamingPartsState => {
    const parts: MessagePart[] = [];

    // Build a set of tool call IDs already present in chainPartsRef so we can
    // detect any that exist only in toolCallMetaRef (e.g. if chainPartsRef was
    // reset before the snapshot was taken in a race condition).
    const chainToolIds = new Set<string>();
    for (const cp of chainPartsRef.current) {
      if (cp.type === 'tool_call') {
        chainToolIds.add((cp as any).id as string);
      }
    }

    if (inToolPhaseRef.current && chainPartsRef.current.length > 0) {
      // ── Tool phase: split text into pre-tool and post-tool segments ──
      // This ensures the rendered order is: initial text → chain → final text
      const preToolLen = Math.min(preToolTextLenRef.current, textRef.current.length);
      const preToolText = textRef.current.slice(0, preToolLen);
      const postToolText = textRef.current.slice(preToolLen);

      // 1. Pre-tool text (initial response before any tool was called)
      const cleanedPreToolText = stripFunctionCallMarkup(preToolText);
      if (cleanedPreToolText) {
        parts.push({
          type: 'text',
          content: cleanedPreToolText,
          isStreaming: false, // Pre-tool text is always complete
        });
      }

      // 2. Chain parts in temporal order (interleaved thinking + tool calls)
      for (const chainPart of chainPartsRef.current) {
        parts.push(chainPart);
      }

      // 3. Tool calls from toolCallMetaRef not already in chainPartsRef.
      //    Serves as a fallback so tool invocations are never silently dropped
      //    when chainPartsRef and toolCallMetaRef fall out of sync (e.g. a race
      //    between finalize() and a concurrent reset() call).
      for (const [, meta] of toolCallMetaRef.current) {
        if (!chainToolIds.has(meta.id)) {
          parts.push({
            type: 'tool_call',
            id: meta.id,
            toolName: meta.toolName,
            status: meta.status,
            args: meta.args,
            result: meta.result,
            error: meta.error,
            durationMs: meta.durationMs,
            label: meta.label,
            description: meta.description,
            sources: meta.sources,
          } as MessagePart);
        }
      }

      // 4. Info messages
      for (const infoPart of infoPartsRef.current) {
        parts.push(infoPart);
      }

      // 5. Post-tool text (response after tools completed)
      const cleanedPostToolText = stripFunctionCallMarkup(postToolText);
      if (cleanedPostToolText) {
        parts.push({
          type: 'text',
          content: cleanedPostToolText,
          isStreaming: streaming && runningToolCountRef.current === 0,
        });
      }
    } else if (toolCallMetaRef.current.size > 0 && chainPartsRef.current.length === 0) {
      // ── Tool calls exist in metadata but chainPartsRef is empty ──
      // This branch handles the case where inToolPhaseRef was not set (e.g. the
      // stream was short-circuited) but tool calls are still tracked in the meta
      // map. Reconstruct tool_call MessageParts so they appear in the output.

      for (const [, meta] of toolCallMetaRef.current) {
        parts.push({
          type: 'tool_call',
          id: meta.id,
          toolName: meta.toolName,
          status: meta.status,
          args: meta.args,
          result: meta.result,
          error: meta.error,
          durationMs: meta.durationMs,
          label: meta.label,
          description: meta.description,
          sources: meta.sources,
        } as MessagePart);
      }

      // Info messages
      for (const infoPart of infoPartsRef.current) {
        parts.push(infoPart);
      }

      // Response text
      const cleanedText = stripFunctionCallMarkup(textRef.current);
      if (cleanedText) {
        parts.push({
          type: 'text',
          content: cleanedText,
          isStreaming: streaming,
        });
      }
    } else {
      // ── No tools: original order (chain parts → info → text) ──

      // 1. Chain parts (reasoning only, before any tool calls)
      for (const chainPart of chainPartsRef.current) {
        parts.push(chainPart);
      }

      // 2. Info messages
      for (const infoPart of infoPartsRef.current) {
        parts.push(infoPart);
      }

      // 3. Text content
      const cleanedText = stripFunctionCallMarkup(textRef.current);
      if (cleanedText) {
        parts.push({
          type: 'text',
          content: cleanedText,
          isStreaming: streaming && chainPartsRef.current.length === 0,
        });
      }
    }

    // Sources (always last)
    for (const source of sourcesRef.current) {
      parts.push({
        type: 'source',
        sourceType: source.sourceType || (source.url?.startsWith('http') ? 'url' : 'document'),
        title: source.title,
        url: source.url,
        description: source.snippet,
        citationNumber: source.citationNumber,
      } as MessagePart);
    }

    // File parts (always last)
    for (const filePart of filesRef.current) {
      parts.push(filePart);
    }

    // Derive flat reasoning string from all thinking segments (backward compat)
    const reasoning = chainPartsRef.current
      .filter((p) => p.type === 'thinking')
      .map((p) => (p as any).thinkingContent || '')
      .join('\n');

    return {
      richParts: parts,
      sources: [...sourcesRef.current],
      files: [...filesRef.current],
      textContent: textRef.current,
      reasoning,
      toolCallMeta: new Map(toolCallMetaRef.current),
      workflowPreview: workflowPreviewRef.current,
      workflowProgress: new Map(workflowProgressRef.current),
      workflowResult: workflowResultRef.current,
    };
  }, []);

  // ── Batched state update via requestAnimationFrame ──

  const scheduleUpdate = useCallback(() => {
    if (rafIdRef.current !== null) return;
    rafIdRef.current = requestAnimationFrame(() => {
      rafIdRef.current = null;
      setState(buildStateFromRefs(true));
    });
  }, [buildStateFromRefs]);

  // ── Shared helper: append text to the active thinking segment, or create one ──

  const appendToActiveThinking = useCallback((content: string) => {
    const idx = activeThinkingIdxRef.current;
    if (idx >= 0 && idx < chainPartsRef.current.length && chainPartsRef.current[idx].type === 'thinking') {
      // Append to existing open thinking segment
      const existing = chainPartsRef.current[idx] as any;
      chainPartsRef.current[idx] = {
        ...existing,
        thinkingContent: (existing.thinkingContent || '') + content,
      };
    } else {
      // Push a new thinking segment
      const newIdx = chainPartsRef.current.length;
      chainPartsRef.current.push({
        type: 'thinking',
        variant: 'accordion',
        thinkingContent: content,
        isThinking: true,
        showElapsed: true,
      } as MessagePart);
      activeThinkingIdxRef.current = newIdx;
    }
    scheduleUpdate();
  }, [scheduleUpdate]);

  // ── Shared helper: append narration text for a completed tool call ──

  const appendToNarration = useCallback((toolCallId: string | null, content: string) => {
    if (!toolCallId) {
      // No completed tool to attach narration to — append to response text
      textRef.current += content;
      scheduleUpdate();
      return;
    }
    const existing = narrationRef.current.get(toolCallId) || '';
    narrationRef.current.set(toolCallId, existing + content);
    // Also update the ToolCallMeta description in real-time
    const meta = toolCallMetaRef.current.get(toolCallId);
    if (meta) {
      toolCallMetaRef.current.set(toolCallId, {
        ...meta,
        description: (existing + content).trim(),
      });
    }
    scheduleUpdate();
  }, [scheduleUpdate]);

  // ── Event handlers ──

  const onContentDelta = useCallback((delta: string) => {
    if (!inToolPhaseRef.current) {
      // Pre-tool phase: normal response text
      textRef.current += delta;
      textActiveRef.current = true;
      scheduleUpdate();
    } else if (runningToolCountRef.current > 0) {
      // During active tool execution(s): thinking/reasoning
      textActiveRef.current = false;
      appendToActiveThinking(delta);
    } else {
      // Between tool calls (all completed, but more may come): narration
      appendToNarration(lastCompletedToolIdRef.current, delta);
    }
  }, [scheduleUpdate, appendToActiveThinking, appendToNarration]);

  const onReasoningDelta = useCallback((content: string) => {
    textActiveRef.current = false;
    appendToActiveThinking(content);
  }, [appendToActiveThinking]);

  const onThinkingDelta = useCallback((delta: string) => {
    textActiveRef.current = false;
    appendToActiveThinking(delta);
  }, [appendToActiveThinking]);

  const onToolCallStart = useCallback((
    toolName: string,
    toolCallId?: string,
    label?: string,
    _message?: string,
    args?: Record<string, unknown>,
    producesFile?: boolean,
  ) => {
    const id = toolCallId || `tc-${Date.now()}`;

    // Enter tool phase — marks that tools have been seen
    if (!inToolPhaseRef.current) {
      preToolTextLenRef.current = textRef.current.length;
    }
    inToolPhaseRef.current = true;
    // Text is no longer actively streaming — hide cursor
    textActiveRef.current = false;
    // Track running tool count for parallel tool calls
    runningToolCountRef.current += 1;

    // Close the active thinking segment (next reasoning delta will start a new one)
    const thinkIdx = activeThinkingIdxRef.current;
    if (thinkIdx >= 0 && thinkIdx < chainPartsRef.current.length && chainPartsRef.current[thinkIdx].type === 'thinking') {
      chainPartsRef.current[thinkIdx] = {
        ...chainPartsRef.current[thinkIdx],
        isThinking: false,
      } as MessagePart;
    }
    activeThinkingIdxRef.current = -1;

    // Push tool call into the ordered chain and record its index
    const toolPart: MessagePart = {
      type: 'tool_call',
      id,
      toolName,
      status: 'running',
      args,
      steps: label ? [{ id: `step-${id}`, label, status: 'active' }] : undefined,
    } as MessagePart;
    toolCallIndexRef.current.set(id, chainPartsRef.current.length);
    chainPartsRef.current.push(toolPart);

    // Store enriched metadata (for Timeline + TimelineEvent)
    toolCallMetaRef.current.set(id, {
      id,
      toolName,
      label,
      status: 'running',
      args,
      producesFile,
      sources: [],
    });
    scheduleUpdate();
  }, [scheduleUpdate]);

  const onToolCallProgress = useCallback((
    toolName: string,
    label?: string,
    _message?: string,
  ) => {
    // Find the matching running tool call in chainPartsRef
    for (const [_id, idx] of toolCallIndexRef.current.entries()) {
      const part = chainPartsRef.current[idx];
      if (part && (part as any).toolName === toolName && (part as any).status === 'running') {
        const updated = { ...part } as any;
        if (label) {
          updated.steps = [...(updated.steps || []), { id: `step-${Date.now()}`, label, status: 'active' }];
        }
        chainPartsRef.current[idx] = updated;
        break;
      }
    }
    scheduleUpdate();
  }, [scheduleUpdate]);

  const onToolCallComplete = useCallback((result: {
    toolCallId?: string;
    tool: string;
    success: boolean;
    result?: unknown;
    error?: string;
    durationMs?: number;
  }) => {
    const id = result.toolCallId;
    // Find by ID or by tool name
    let targetId: string | undefined;
    if (id && toolCallIndexRef.current.has(id)) {
      targetId = id;
    } else {
      // Find first running tool call matching the name
      for (const [tcId, idx] of toolCallIndexRef.current.entries()) {
        const part = chainPartsRef.current[idx];
        if (part && (part as any).toolName === result.tool && (part as any).status === 'running') {
          targetId = tcId;
          break;
        }
      }
    }

    if (targetId) {
      const idx = toolCallIndexRef.current.get(targetId)!;
      // Update MessagePart in chainPartsRef
      const existing = chainPartsRef.current[idx] as any;
      chainPartsRef.current[idx] = {
        ...existing,
        status: result.success ? 'success' : 'error',
        result: result.result,
        error: result.error,
        durationMs: result.durationMs,
      };
      // Attach any accumulated narration as description
      const narration = narrationRef.current.get(targetId);
      // Update ToolCallMeta
      const meta = toolCallMetaRef.current.get(targetId);
      if (meta) {
        toolCallMetaRef.current.set(targetId, {
          ...meta,
          status: result.success ? 'success' : 'error',
          result: result.result,
          error: result.error,
          durationMs: result.durationMs,
          description: narration ? stripFunctionCallMarkup(narration.trim()) : meta.description,
        });
      }
    }

    // Decrement running count and track last completed
    runningToolCountRef.current = Math.max(0, runningToolCountRef.current - 1);
    if (targetId) {
      lastCompletedToolIdRef.current = targetId;
    }

    scheduleUpdate();
  }, [scheduleUpdate]);

  const onSource = useCallback((source: SourceItem & { toolCallId?: string }) => {
    sourceCountRef.current += 1;
    const indexed: IndexedSource = {
      ...source,
      citationNumber: sourceCountRef.current,
    };
    // Add to aggregated list (always)
    sourcesRef.current = [...sourcesRef.current, indexed];
    // Also add to specific tool call meta if toolCallId present
    if (source.toolCallId) {
      const meta = toolCallMetaRef.current.get(source.toolCallId);
      if (meta) {
        toolCallMetaRef.current.set(source.toolCallId, {
          ...meta,
          sources: [...meta.sources, { title: source.title, url: source.url, snippet: source.snippet }],
        });
      }
    }
    scheduleUpdate();
  }, [scheduleUpdate]);

  const onInfoMessage = useCallback((message: string, title?: string) => {
    infoPartsRef.current = [...infoPartsRef.current, {
      type: 'info',
      message,
      title,
    } as MessagePart];
    scheduleUpdate();
  }, [scheduleUpdate]);

  const onPart = useCallback((partData: Record<string, unknown>) => {
    const partType = partData.type as string;
    const toolCallId = partData.toolCallId as string | undefined;

    if (partType === 'file') {
      filesRef.current = [...filesRef.current, {
        type: 'file',
        filename: (partData.filename as string) || 'untitled',
        size: partData.size as number | undefined,
        fileType: partData.fileType as string | undefined,
        downloadUrl: partData.downloadUrl as string | undefined,
        content: partData.content as string | undefined,
        toolCallId,
      } as MessagePart];
    } else if (partType === 'table') {
      infoPartsRef.current = [...infoPartsRef.current, {
        type: 'table',
        columns: partData.columns as any[] || [],
        data: (partData.data || partData.rows) as Record<string, unknown>[] || [],
        title: partData.title as string | undefined,
      } as MessagePart];
    } else if (partType === 'metric') {
      infoPartsRef.current = [...infoPartsRef.current, {
        type: 'metric',
        label: (partData.label as string) || '',
        value: partData.value as string | number,
        change: partData.change as string | undefined,
      } as MessagePart];
    } else if (partType === 'chart') {
      infoPartsRef.current = [...infoPartsRef.current, {
        type: 'chart',
        chartType: (partData.chartType as string) || 'bar',
        data: partData.data,
        title: partData.title as string | undefined,
      } as unknown as MessagePart];
    } else if (partType === 'json') {
      infoPartsRef.current = [...infoPartsRef.current, {
        type: 'json',
        data: partData.data,
        title: partData.title as string | undefined,
      } as MessagePart];
    } else if (partType === 'image') {
      infoPartsRef.current = [...infoPartsRef.current, {
        type: 'image',
        src: (partData.src as string) || '',
        alt: partData.alt as string | undefined,
        caption: partData.caption as string | undefined,
      } as MessagePart];
    } else if (partType === 'diff') {
      infoPartsRef.current = [...infoPartsRef.current, {
        type: 'diff',
        diff: partData.content as string | undefined,
        title: partData.title as string | undefined,
      } as MessagePart];
    } else if (partType === 'memory_saved') {
      infoPartsRef.current = [...infoPartsRef.current, {
        type: 'memory_saved',
        memoryId: partData.memoryId as string,
        snippet: (partData.snippet as string) || '',
        agentName: partData.agentName as string | undefined,
        actionType: partData.actionType as string | undefined,
        toolCallId,
      } as MessagePart];
    }

    scheduleUpdate();
  }, [scheduleUpdate]);

  const onError = useCallback((_message: string) => {
    // Error handling is managed by ChatInterface (throws to catch block)
    // The hook just needs to accept the callback to satisfy SSEEventHandlers
  }, []);

  // onDone is handled by the caller (ChatInterface) for session persistence
  const onDone = useCallback((_data: any) => {
    // Mark text as no longer streaming
    scheduleUpdate();
  }, [scheduleUpdate]);

  // ── Workflow event handlers ──

  const onWorkflowPreview = useCallback((data: any) => {
    workflowPreviewRef.current = {
      status: data.status ?? 'pending_approval',
      workflow_id: data.workflow_id,
      workflow_name: data.workflow_name,
      steps: data.steps || [],
      has_hitl_nodes: !!data.has_hitl_nodes,
      hitl_warning: data.hitl_warning ?? null,
      input_summary: data.input_summary || {},
    } as WorkflowPreview;
    scheduleUpdate();
  }, [scheduleUpdate]);

  const onWorkflowProgress = useCallback((data: any) => {
    const event: WorkflowProgressEvent = {
      workflowId: data.workflowId,
      stepId: data.stepId,
      label: data.label,
      status: data.status,
      message: data.message,
      elapsedMs: data.elapsedMs,
    };
    workflowProgressRef.current = new Map(workflowProgressRef.current);
    workflowProgressRef.current.set(data.stepId, event);
    scheduleUpdate();
  }, [scheduleUpdate]);

  const onWorkflowComplete = useCallback((data: any) => {
    workflowResultRef.current = {
      workflowId: data.workflowId,
      workflowName: data.workflowName,
      results: data.results || {},
      executedNodes: data.executedNodes,
      totalNodes: data.totalNodes,
      elapsedMs: data.elapsedMs,
    } as WorkflowCompleteEvent;
    scheduleUpdate();
  }, [scheduleUpdate]);

  const handlers: SSEEventHandlers = {
    onContentDelta,
    onReasoningDelta,
    onThinkingDelta,
    onToolCallStart,
    onToolCallProgress,
    onToolCallComplete,
    onSource,
    onInfoMessage,
    onPart,
    onError,
    onDone,
    onWorkflowPreview,
    onWorkflowProgress,
    onWorkflowComplete,
  };

  const reset = useCallback(() => {
    textRef.current = '';
    textActiveRef.current = false;
    chainPartsRef.current = [];
    toolCallIndexRef.current.clear();
    activeThinkingIdxRef.current = -1;
    inToolPhaseRef.current = false;
    preToolTextLenRef.current = 0;
    runningToolCountRef.current = 0;
    lastCompletedToolIdRef.current = null;
    narrationRef.current.clear();
    toolCallMetaRef.current.clear();
    infoPartsRef.current = [];
    filesRef.current = [];
    sourcesRef.current = [];
    sourceCountRef.current = 0;
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    workflowPreviewRef.current = null;
    workflowProgressRef.current = new Map();
    workflowResultRef.current = null;
    setState({ richParts: [], sources: [], files: [], textContent: '', reasoning: '', toolCallMeta: new Map(), workflowPreview: null, workflowProgress: new Map(), workflowResult: null });
  }, []);

  /**
   * Mark all parts as finalized (not streaming).
   * Cancels any pending rAF and synchronously flushes from refs so that
   * streamingRichPartsRef.current in ChatInterface is never stale.
   *
   * Content routing on finalize:
   *   - Narration for the last completed tool → ToolCallMeta.description
   *   - Any trailing narration/thinking after the final tool → response text (textRef)
   */
  const finalize = useCallback(() => {
    // Cancel pending rAF — we'll flush synchronously
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }

    // Close any open thinking segment
    activeThinkingIdxRef.current = -1;

    // Move trailing narration (response text after the final tool) to textRef.
    // After all tools complete, onContentDelta routes deltas into narration for
    // the last tool. That text is the actual response — not a tool description.
    // Pre-tool text in textRef is concatenated with post-tool narration (no separator).
    if (inToolPhaseRef.current && lastCompletedToolIdRef.current) {
      const rawNarration = narrationRef.current.get(lastCompletedToolIdRef.current);
      // Strip raw function call XML that may leak from the backend
      const lastNarration = rawNarration ? stripFunctionCallMarkup(rawNarration) : '';
      if (lastNarration.trim()) {
        // Concatenate directly — preserves mid-word joins and natural token spacing.
        // Raw concat (no '\n\n') ensures split words rejoin: "H" + "ere..." = "Here..."
        textRef.current = (textRef.current + lastNarration).trim();
        narrationRef.current.delete(lastCompletedToolIdRef.current);
        // Keep meta.description intact — it provides reasoning context in the
        // ChainOfThought view even after streaming ends. Clearing it caused
        // the last tool's reasoning to vanish when the response finalized.
      } else {
        // Narration was only function call XML — discard it entirely
        narrationRef.current.delete(lastCompletedToolIdRef.current);
      }
    }

    // Flush remaining (inter-tool) narration to ToolCallMeta descriptions
    for (const [tcId, text] of narrationRef.current.entries()) {
      const cleanedText = stripFunctionCallMarkup(text);
      if (cleanedText) {
        const meta = toolCallMetaRef.current.get(tcId);
        if (meta) {
          toolCallMetaRef.current.set(tcId, {
            ...meta,
            description: cleanedText,
          });
        }
      }
    }

    // Mark all thinking segments as finalized (no longer streaming)
    for (let i = 0; i < chainPartsRef.current.length; i++) {
      if (chainPartsRef.current[i].type === 'thinking') {
        chainPartsRef.current[i] = { ...chainPartsRef.current[i], isThinking: false } as MessagePart;
      }
    }

    // Merge ephemeral ToolCallMeta into chainPartsRef so label/description
    // survive persistence (they'd otherwise be lost with the ref)
    for (const [tcId, meta] of toolCallMetaRef.current.entries()) {
      const idx = toolCallIndexRef.current.get(tcId);
      if (idx != null && idx < chainPartsRef.current.length) {
        const part = chainPartsRef.current[idx];
        if (part.type === 'tool_call') {
          chainPartsRef.current[idx] = {
            ...part,
            ...(meta.label ? { label: meta.label } : {}),
            ...(meta.description ? { description: meta.description } : {}),
            ...(meta.sources?.length ? { sources: meta.sources } : {}),
          };
        }
      }
    }

    // Synchronously build final state from refs (not from stale prev state)
    setState(buildStateFromRefs(false));
  }, [buildStateFromRefs]);

  /**
   * Read the current finalized snapshot directly from refs.
   * Use this instead of the stale React state when you need the latest parts
   * synchronously after finalize() has been called (before React re-renders).
   */
  const getSnapshot = useCallback((): StreamingPartsState => {
    return buildStateFromRefs(false);
  }, [buildStateFromRefs]);

  return {
    ...state,
    handlers,
    reset,
    finalize,
    getSnapshot,
  };
}
