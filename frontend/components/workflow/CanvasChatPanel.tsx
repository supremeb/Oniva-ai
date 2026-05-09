'use client';

import { useState, useRef, useEffect, useCallback, memo, Component, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import {
  PaperPlaneTilt,
  CircleNotch,
  WarningCircle,
  ArrowClockwise,
  Check,
  X,
  CaretLeft,
  CaretRight,
} from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { GenerativeO } from '@/components/ui/GenerativeO';
import { usePersistedChat } from '@/hooks/usePersistedChat';
import type { ChatMessage, ChatMessageType, ConnectorInfo, QuestionOption } from '@/lib/models/chat-messages';
import {
  createUserMessage,
  createThinkingMessage,
  createAssistantMessage,
  createErrorMessage,
} from '@/lib/models/chat-messages';
import type { Node, Edge } from '@xyflow/react';
import { API_BASE_URL } from '@/lib/api';
import { useWorkflowStore } from '@/lib/stores/workflow';

// Simple Error Boundary component
interface ErrorBoundaryProps {
  children: ReactNode;
  FallbackComponent: React.ComponentType<{ error: Error; resetErrorBoundary: () => void }>;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  resetErrorBoundary = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError && this.state.error) {
      const { FallbackComponent } = this.props;
      return <FallbackComponent error={this.state.error} resetErrorBoundary={this.resetErrorBoundary} />;
    }
    return this.props.children;
  }
}

interface CanvasChatPanelProps {
  workflowId: string;
  nodes: Node[];
  edges: Edge[];
  onNodesGenerated: (nodes: Node[], edges: Edge[]) => void;
  onNodeSelect?: (nodeId: string) => void;
  isReadOnly?: boolean;
  className?: string;
}

// Error fallback component
function ChatErrorFallback({ error, resetErrorBoundary }: { error: Error; resetErrorBoundary: () => void }) {
  return (
    <div className="p-4 bg-red-50 border border-red-200 rounded-lg m-4">
      <p className="text-sm text-red-600 mb-2">Chat panel encountered an error</p>
      <Button size="sm" variant="outline" onClick={resetErrorBoundary}>
        <ArrowClockwise className="h-3 w-3 mr-1" />
        Retry
      </Button>
    </div>
  );
}

/**
 * CanvasChatPanel - Side panel for AI-assisted workflow building
 *
 * Features:
 * - Full-height side panel (35% width)
 * - Smooth collapse/expand animation
 * - Resizable width
 * - Persistent chat history
 * - SSE streaming responses with line buffering
 * - GenerativeO animated logo (Oniva branding)
 * - Nodes preview with Apply/Cancel via Zustand store
 * - Keyboard shortcuts (Cmd+K to toggle, Escape to close)
 * - Wrapped in Error Boundary for isolation
 */
function CanvasChatPanelInner({
  workflowId,
  nodes,
  edges,
  onNodesGenerated,
  onNodeSelect,
  isReadOnly = false,
  className,
}: CanvasChatPanelProps) {
  const {
    isCanvasChatOpen,
    toggleCanvasChat,
    setCanvasChatOpen,
    setPending,
    discardPending,
  } = useWorkflowStore();

  // Resizable width state
  const [panelWidth, setPanelWidth] = useState(360);
  const [isResizing, setIsResizing] = useState(false);
  const resizeRef = useRef<HTMLDivElement>(null);
  const minWidth = 300;
  const maxWidth = 500;

  const {
    messages,
    addMessage,
    updateMessage,
    removeMessage,
    isLoaded,
  } = usePersistedChat({
    workflowId,
    stepCount: nodes.length,
  });

  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<
    'idle' | 'connecting' | 'streaming' | 'reconnecting' | 'failed'
  >('idle');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Handle resize
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) return;
      const newWidth = e.clientX;
      setPanelWidth(Math.min(Math.max(newWidth, minWidth), maxWidth));
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing]);

  // Keyboard shortcuts: Cmd+K to open, Escape to close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Cmd+K or Ctrl+K to toggle
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        toggleCanvasChat();
      }
      // Escape to close when open
      if (e.key === 'Escape' && isCanvasChatOpen) {
        setCanvasChatOpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isCanvasChatOpen, toggleCanvasChat, setCanvasChatOpen]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'inherit';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = `${Math.min(scrollHeight, 120)}px`;
    }
  }, [input]);

  // Simple node placement: place new nodes to the right of rightmost existing node
  const placeNewNodes = useCallback((existingNodes: Node[], newNodes: Node[]): Node[] => {
    const rightmost = existingNodes.length > 0
      ? Math.max(...existingNodes.map(n => n.position.x))
      : 0;

    return newNodes.map((node, index) => ({
      ...node,
      position: {
        x: rightmost + 250 + (index * 200),
        y: 100 + (index * 50),
      },
    }));
  }, []);

  // Handle sending a message
  const handleSend = useCallback(async () => {
    if (!input.trim() || isStreaming || isReadOnly) return;

    const userMessage = createUserMessage(input.trim());
    addMessage(userMessage);
    setInput('');
    setIsStreaming(true);
    setConnectionStatus('connecting');

    // Add thinking message
    const thinkingId = `thinking-${Date.now()}`;
    addMessage({ ...createThinkingMessage(), id: thinkingId });

    try {
      // Convert nodes to the step format expected by the API
      const currentSteps = nodes.map((node) => ({
        id: node.id,
        type: node.type || 'llm',
        label: (node.data as Record<string, unknown>)?.label || node.type || 'Step',
        data: node.data as Record<string, unknown>,
      }));

      const csrfHeaders: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      const csrfMatch = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
      if (csrfMatch) {
        csrfHeaders['X-CSRF-Token'] = decodeURIComponent(csrfMatch[1]);
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/workflow-ai/chat`, {
        method: 'POST',
        headers: csrfHeaders,
        credentials: 'include',
        body: JSON.stringify({
          workflow_id: workflowId,
          message: input.trim(),
          current_steps: currentSteps,
          current_edges: edges.map(e => ({
            source: e.source,
            target: e.target,
            sourceHandle: e.sourceHandle || null,
            targetHandle: e.targetHandle || null,
          })),
          conversation_history: messages.slice(-10).map((m) => ({
            role: m.role,
            content: m.content,
          })),
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      // Remove thinking message
      removeMessage(thinkingId);

      // Handle streaming response
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No response body');
      }

      setConnectionStatus('streaming');

      const decoder = new TextDecoder();
      let assistantContent = '';
      const assistantId = `assistant-${Date.now()}`;
      // Buffer for incomplete SSE lines split across chunks
      let lineBuffer = '';

      // Add empty assistant message that we'll update
      addMessage({
        ...createAssistantMessage(''),
        id: assistantId,
      });
      setStreamingMessageId(assistantId);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        // Prepend any buffered partial line from previous chunk
        const text = lineBuffer + chunk;
        const lines = text.split('\n');
        // Last element may be incomplete — buffer it for next iteration
        lineBuffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;

          let data: Record<string, unknown>;
          try {
            data = JSON.parse(line.slice(6));
          } catch {
            // Incomplete JSON — skip this line
            continue;
          }

          try {
            if (data.type === 'content') {
              assistantContent += data.content as string;
              updateMessage(assistantId, { content: assistantContent });
            } else if (data.type === 'steps_preview') {
              // Handle steps preview - convert step proposals to React Flow nodes
              const steps = (data.steps || []) as Array<{
                id: string;
                type: string;
                label: string;
                config: Record<string, unknown>;
                action?: string;
                position?: number;
              }>;

              // Remap IDs: only remap for "add" actions to avoid collisions.
              // Keep original IDs for "modify" and "delete" so they target existing nodes.
              const existingIds = new Set(nodes.map((n) => n.id));
              const ts = Date.now();
              const idMap = new Map<string, string>();
              steps.forEach((s, i) => {
                if (s.action === 'modify' || s.action === 'delete') {
                  idMap.set(s.id, s.id);
                } else if (existingIds.has(s.id)) {
                  const newId = `${s.type}-ai-${ts}-${i}`;
                  idMap.set(s.id, newId);
                } else {
                  idMap.set(s.id, s.id);
                }
              });

              // Update template variable references (e.g., {{old-id.result}} → {{new-id.result}})
              const remapTemplateRefs = (config: Record<string, unknown>): Record<string, unknown> => {
                const remapped = { ...config };
                for (const [key, val] of Object.entries(remapped)) {
                  if (typeof val === 'string') {
                    let updated = val;
                    for (const [oldId, newId] of idMap.entries()) {
                      if (oldId !== newId) {
                        updated = updated.replaceAll(`{{${oldId}.`, `{{${newId}.`);
                        updated = updated.replaceAll(`{{${oldId}}}`, `{{${newId}}}`);
                      }
                    }
                    remapped[key] = updated;
                  }
                }
                return remapped;
              };

              const previewNodes: Node[] = steps
                .filter((s) => s.action !== 'delete')
                .map((s) => ({
                  id: idMap.get(s.id)!,
                  type: s.type,
                  position: { x: 0, y: 0 },
                  data: { label: s.label, ...remapTemplateRefs(s.config), _action: s.action },
                }));

              // Use AI-proposed edges when available, fall back to sequential chaining
              const aiEdges = (data.proposed_edges || []) as Array<{
                source: string;
                target: string;
                sourceHandle?: string | null;
                targetHandle?: string | null;
              }>;

              let previewEdges: Edge[];
              if (aiEdges.length > 0) {
                previewEdges = aiEdges.map((e) => ({
                  id: `edge-${e.source}-${e.target}-${e.sourceHandle || 'default'}`,
                  source: idMap.get(e.source) || e.source,
                  target: idMap.get(e.target) || e.target,
                  sourceHandle: e.sourceHandle || null,
                  targetHandle: e.targetHandle || null,
                  type: 'smoothstep',
                }));
              } else {
                // Fallback: sequential chaining (backward compat)
                const addedSteps = steps.filter((s) => s.action !== 'delete');
                previewEdges = addedSteps.slice(1).map((s, i) => ({
                  id: `edge-${idMap.get(addedSteps[i].id)}-${idMap.get(s.id)}`,
                  source: idMap.get(addedSteps[i].id)!,
                  target: idMap.get(s.id)!,
                  type: 'smoothstep',
                }));
              }

              // Position new nodes
              const positionedNodes = placeNewNodes(nodes, previewNodes);

              // Ghost preview on canvas (Zustand — may be overwritten by later proposals)
              setPending(positionedNodes, previewEdges);

              // Store proposed nodes/edges per-message in metadata
              // (per-message — each Apply button reads its own data, not shared global)
              updateMessage(assistantId, {
                type: 'steps_preview' as ChatMessageType,
                metadata: {
                  type: 'steps_preview',
                  data: {
                    steps: [],
                    applied: false,
                    pendingApproval: true,
                    proposedNodes: positionedNodes,
                    proposedEdges: previewEdges,
                  },
                },
              });
            } else if (data.type === 'oauth_request') {
              updateMessage(assistantId, {
                type: 'oauth_request' as ChatMessageType,
                metadata: {
                  type: 'oauth_request',
                  data: {
                    connectors: data.connectors as ConnectorInfo[],
                    message: data.message as string,
                  },
                },
              });
            } else if (data.type === 'question') {
              updateMessage(assistantId, {
                type: 'question' as ChatMessageType,
                metadata: {
                  type: 'question',
                  data: {
                    options: data.options as Array<{ id: string; label: string; value: string }>,
                    allowMultiple: (data.allowMultiple as boolean) || false,
                    selectedValues: [],
                  },
                },
              });
            } else if (data.type === 'done') {
              break;
            } else if (data.type === 'error') {
              throw new Error((data.message as string) || 'Unknown error');
            }
          } catch (eventError) {
            // Re-throw errors from 'error' events, log everything else
            if (eventError instanceof Error && data.type === 'error') {
              throw eventError;
            }
            console.error('[CanvasChat] Error handling SSE event:', data.type, eventError);
          }
        }
      }

      setConnectionStatus('idle');
      setStreamingMessageId(null);
    } catch (error) {
      console.error('Chat error:', error);
      removeMessage(thinkingId);
      addMessage(
        createErrorMessage(
          error instanceof Error ? error.message : 'Failed to get response'
        )
      );
      setConnectionStatus('failed');
      setStreamingMessageId(null);
    } finally {
      setIsStreaming(false);
    }
  }, [
    input,
    isStreaming,
    isReadOnly,
    workflowId,
    nodes,
    edges,
    messages,
    addMessage,
    updateMessage,
    removeMessage,
    placeNewNodes,
  ]);

  // Handle Enter key
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Handle option click for question messages
  const handleOptionClick = useCallback(
    (messageId: string, optionValue: string) => {
      // Send option as user message
      const optionMessage = createUserMessage(optionValue);
      addMessage(optionMessage);

      // Mark option as selected in the original question
      updateMessage(messageId, {
        metadata: {
          type: 'question',
          data: {
            options: [],
            selectedValues: [optionValue],
          },
        },
      });

      // Trigger follow-up
      setInput(optionValue);
      setTimeout(() => handleSend(), 100);
    },
    [addMessage, updateMessage, handleSend]
  );

  // Handle apply nodes — read proposed data from per-message metadata
  const handleApplyNodes = useCallback(
    (messageId: string) => {
      const msg = messages.find(m => m.id === messageId);
      const meta = msg?.metadata;
      if (meta?.type === 'steps_preview' && meta.data.proposedNodes?.length) {
        onNodesGenerated(meta.data.proposedNodes, meta.data.proposedEdges || []);
      }

      // Clear ghost preview
      discardPending();

      updateMessage(messageId, {
        metadata: {
          type: 'steps_preview',
          data: {
            steps: [],
            applied: true,
            pendingApproval: false,
          },
        },
      });
    },
    [updateMessage, onNodesGenerated, messages, discardPending]
  );

  // Handle discard nodes
  const handleDiscardNodes = useCallback(
    (messageId: string) => {
      // Clear ghost preview
      discardPending();

      updateMessage(messageId, {
        metadata: {
          type: 'steps_preview',
          data: {
            steps: [],
            applied: false,
            pendingApproval: false,
          },
        },
      });
    },
    [updateMessage, discardPending]
  );

  const canSubmit = input.trim() && !isStreaming && !isReadOnly;

  // Collapsed state - full height rail with vertical text
  if (!isCanvasChatOpen) {
    return (
      <button
        onClick={toggleCanvasChat}
        className={cn(
          'h-full w-10 flex flex-col items-center justify-center gap-3',
          'bg-white border-r border-slate-200',
          'hover:bg-slate-100 transition-all duration-200 ease-out',
          'focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary/20',
          'group'
        )}
        aria-label="Open Oniva AI assistant"
        title="Open Oniva AI (Cmd+K)"
      >
        <GenerativeO size={20} animating={false} />
        <span
          className="text-xs font-medium text-slate-500 group-hover:text-slate-700 transition-colors"
          style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
        >
          Workflow AI
        </span>
        <CaretRight className="h-4 w-4 text-slate-400 group-hover:text-slate-600 transition-colors" weight="bold" />
      </button>
    );
  }

  // Loading skeleton
  if (!isLoaded) {
    return (
      <div
        style={{ width: panelWidth }}
        className={cn(
          'h-full bg-white border-r border-slate-200 flex flex-col',
          'transition-all duration-300 ease-out',
          className
        )}
      >
        <div className="p-4 animate-pulse space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i}>
              <div className="h-4 bg-slate-200 rounded w-3/4" />
              <div className="h-3 bg-slate-200 rounded w-1/2 mt-2" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Expanded state - full side panel
  return (
    <div
      ref={resizeRef}
      style={{ width: panelWidth }}
      className={cn(
        'h-full bg-white border-r border-slate-200 flex flex-col relative',
        'transition-[width] duration-300 ease-out',
        className
      )}
    >
      {/* Resize handle */}
      <div
        className={cn(
          'absolute top-0 right-0 w-1 h-full cursor-col-resize z-10',
          'hover:bg-slate-200 transition-colors',
          isResizing && 'bg-primary/30'
        )}
        onMouseDown={() => setIsResizing(true)}
      />

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
        <div className="flex items-center gap-2">
          <GenerativeO size={20} animating={isStreaming} />
          <span className="text-sm font-semibold text-slate-800">Oniva AI</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setCanvasChatOpen(false)}
          className="h-7 w-7 rounded-full hover:bg-slate-100"
          title="Collapse panel (Escape)"
          aria-label="Collapse chat panel"
        >
          <CaretLeft className="h-4 w-4 text-slate-500" weight="bold" />
        </Button>
      </div>

      {/* Connection status */}
      {connectionStatus === 'reconnecting' && (
        <div className="flex items-center gap-2 text-xs text-amber-600 p-2 bg-amber-50 border-b border-amber-100">
          <CircleNotch className="h-3 w-3 animate-spin" />
          Reconnecting...
        </div>
      )}
      {connectionStatus === 'failed' && (
        <div className="flex items-center gap-2 text-xs text-red-600 p-2 bg-red-50 border-b border-red-100">
          <WarningCircle className="h-3 w-3" />
          Connection lost.
          <Button
            size="sm"
            variant="link"
            className="h-auto p-0 text-xs"
            onClick={() => setConnectionStatus('idle')}
          >
            <ArrowClockwise className="h-3 w-3 mr-1" />
            Retry
          </Button>
        </div>
      )}

      {/* Messages */}
      <div className={cn(
        'flex-1 overflow-y-auto',
        messages.length === 0 ? 'flex items-center justify-center' : 'p-4 space-y-4'
      )}>
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center px-6">
            <GenerativeO size={56} animating={false} />
            <p className="mt-4 text-base font-medium text-slate-800">
              Workflow AI Assistant
            </p>
            <p className="mt-2 text-sm text-slate-500 max-w-[240px]">
              Ask me to help build, modify, or optimize your workflow
            </p>
            <div className="mt-6 flex flex-col gap-2 w-full max-w-[240px]">
              <button
                onClick={() => setInput('Add an LLM node to process text')}
                className="text-xs text-left px-3 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors text-slate-600"
              >
                &ldquo;Add an LLM node to process text&rdquo;
              </button>
              <button
                onClick={() => setInput('Create a workflow for data analysis')}
                className="text-xs text-left px-3 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors text-slate-600"
              >
                &ldquo;Create a workflow for data analysis&rdquo;
              </button>
            </div>
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <ChatMessageBubble
                key={message.id}
                message={message}
                isStreaming={message.id === streamingMessageId}
                onOptionClick={(value) => handleOptionClick(message.id, value)}
                onApplyNodes={() => handleApplyNodes(message.id)}
                onDiscardNodes={() => handleDiscardNodes(message.id)}
                onNodeClick={onNodeSelect}
              />
            ))}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input */}
      {!isReadOnly && (
        <div className="p-3 border-t border-slate-100 shrink-0 bg-slate-50/50">
          <div
            className={cn(
              'flex items-end gap-2 rounded-xl border bg-white',
              'transition-all duration-200',
              'focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary/50',
              'px-3 py-2 shadow-sm'
            )}
          >
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask anything..."
              disabled={isStreaming}
              className={cn(
                'flex-1 resize-none bg-transparent',
                'border-none shadow-none focus-visible:ring-0 focus-visible:ring-offset-0',
                'placeholder:text-slate-400',
                'min-h-[36px] max-h-[120px] text-sm py-1'
              )}
              rows={1}
            />
            <Button
              size="icon"
              onClick={handleSend}
              disabled={!canSubmit}
              className={cn(
                'shrink-0 rounded-lg h-8 w-8',
                'transition-all duration-200'
              )}
            >
              {isStreaming ? (
                <CircleNotch className="h-4 w-4 animate-spin" />
              ) : (
                <PaperPlaneTilt className="h-4 w-4" weight="fill" />
              )}
            </Button>
          </div>
          <p className="text-[10px] text-slate-400 mt-2 text-center">
            Press ⌘K to toggle • Enter to send
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * ChatMessageBubble - Renders a single chat message with Oniva branding
 */
interface ChatMessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  onOptionClick?: (value: string) => void;
  onApplyNodes?: () => void;
  onDiscardNodes?: () => void;
  onNodeClick?: (nodeId: string) => void;
}

const ChatMessageBubble = memo(function ChatMessageBubble({
  message,
  isStreaming = false,
  onOptionClick,
  onApplyNodes,
  onDiscardNodes,
}: ChatMessageBubbleProps) {
  const isUser = message.role === 'user';
  const isThinking = message.type === 'thinking';

  // Thinking state - Show animated GenerativeO
  if (isThinking) {
    return (
      <div className="flex gap-3">
        <div className="flex items-center justify-center w-7 h-7 shrink-0">
          <GenerativeO size={20} animating={true} />
        </div>
        <div className="flex items-center gap-2 rounded-xl rounded-bl-md bg-slate-100 px-4 py-2">
          <span className="text-sm text-slate-500">Thinking...</span>
        </div>
      </div>
    );
  }

  // User message - Right aligned, primary color bubble
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md px-4 py-2.5 bg-primary text-primary-foreground shadow-sm">
          <p className="text-sm whitespace-pre-wrap leading-relaxed">{message.content}</p>
        </div>
      </div>
    );
  }

  // Assistant message - Left aligned with GenerativeO avatar
  return (
    <div className="flex gap-3 group">
      <div className="flex items-start justify-center w-7 h-7 shrink-0 pt-0.5">
        <GenerativeO size={20} animating={isStreaming} />
      </div>
      <div className="flex flex-col max-w-[90%]">
        {/* Message content */}
        <div className="text-slate-700">
          <p className="text-sm whitespace-pre-wrap leading-relaxed">
            {message.content}
            {isStreaming && (
              <span className="inline-block w-1.5 h-4 bg-primary ml-0.5 animate-pulse rounded-sm" />
            )}
          </p>
        </div>

        {/* Error styling */}
        {message.type === 'error' && (
          <div className="mt-2 text-xs text-red-600 bg-red-50 px-3 py-2 rounded-lg">
            {message.content}
          </div>
        )}

        {/* Nodes preview with Apply/Cancel */}
        {message.type === 'steps_preview' &&
          message.metadata?.type === 'steps_preview' &&
          message.metadata.data.pendingApproval && (
            <div className="mt-3 flex gap-2">
              <Button size="sm" onClick={onApplyNodes} className="rounded-lg h-8 text-xs px-4">
                <Check className="h-3.5 w-3.5 mr-1.5" />
                Apply Changes
              </Button>
              <Button size="sm" variant="outline" onClick={onDiscardNodes} className="rounded-lg h-8 text-xs px-4">
                <X className="h-3.5 w-3.5 mr-1.5" />
                Cancel
              </Button>
            </div>
          )}

        {/* Applied confirmation */}
        {message.type === 'steps_preview' &&
          message.metadata?.type === 'steps_preview' &&
          message.metadata.data.applied && (
            <div className="mt-2 text-xs text-green-600 flex items-center gap-1.5 bg-green-50 px-3 py-1.5 rounded-lg w-fit">
              <Check className="h-3.5 w-3.5" />
              Changes applied
            </div>
          )}

        {/* Question options */}
        {message.type === 'question' &&
          message.metadata?.type === 'question' &&
          message.metadata.data.selectedValues?.length === 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {message.metadata.data.options.map((option) => (
                <Button
                  key={option.id}
                  size="sm"
                  variant="outline"
                  onClick={() => onOptionClick?.(option.value)}
                  className="rounded-lg h-8 text-xs"
                >
                  {option.label}
                </Button>
              ))}
            </div>
          )}
      </div>
    </div>
  );
});

/**
 * CanvasChatPanel - Main export wrapped in Error Boundary
 */
export function CanvasChatPanel(props: CanvasChatPanelProps) {
  return (
    <ErrorBoundary FallbackComponent={ChatErrorFallback}>
      <CanvasChatPanelInner {...props} />
    </ErrorBoundary>
  );
}
