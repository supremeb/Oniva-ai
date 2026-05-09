'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import {
  Robot,
  X,
  Lightning,
  ChatCircleDots,
  ClockCounterClockwise,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { API_BASE_URL } from '@/lib/api';
import { useChatPanelStore } from '@/lib/stores/chat-panel';
import { useBackgroundJobStore } from '@/lib/stores/background-job';
import { useNavigationGuard } from '@/hooks/useNavigationGuard';
import { ExecutionTrace } from '@/components/chat-panel/ExecutionTrace';
import { ChatHistory } from '@/components/chat-panel/ChatHistory';
import { BackgroundJobIndicator } from '@/components/chat-panel/BackgroundJobIndicator';

// Import shared chat components
import {
  ChatEmptyStateCompact,
  ChatLoadingIndicator,
  ChatInputField,
  ChatErrorBoundary,
  type LegacyMessage,
} from '@/components/chat/shared';

// Import OutputDisplay for structured workflow outputs
import { OutputDisplay, type WorkflowOutputNode } from './OutputDisplay';

import type { Node, Edge } from '@xyflow/react';
import type { ExecutionTraceStep } from '@/lib/types/chat-panels';

/**
 * Extended message type that includes workflow outputs.
 * Used for displaying structured output from workflow execution.
 */
interface WorkflowMessage extends LegacyMessage {
  outputs?: WorkflowOutputNode[];
}

// Conditionally import Panel from @xyflow/react - only used when embedded in ReactFlow
let Panel: React.ComponentType<{ position: string; className?: string; children: React.ReactNode }> | null = null;
try {
  Panel = require('@xyflow/react').Panel;
} catch {
  // Panel not available outside ReactFlow context
}

/**
 * Custom message item component that renders workflow outputs.
 * Handles both regular text messages and structured output displays.
 */
function WorkflowMessageItem({ message }: { message: WorkflowMessage }) {
  const isUser = message.role === 'user';
  const hasOutputs = message.outputs && message.outputs.length > 0;

  return (
    <div className={cn('flex gap-2', isUser ? 'justify-end' : 'justify-start')}>
      {/* Avatar for assistant */}
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-blue-500/20 to-cyan-500/20 flex items-center justify-center shrink-0">
          <Robot className="h-3.5 w-3.5 text-blue-600" weight="duotone" />
        </div>
      )}

      {/* Message content */}
      <div
        className={cn(
          'max-w-[85%] rounded-xl px-3 py-2',
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted'
        )}
      >
        {hasOutputs ? (
          // Render structured outputs
          <OutputDisplay outputs={message.outputs!} compact />
        ) : (
          // Render regular text content
          <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        )}
      </div>

      {/* Avatar for user */}
      {isUser && (
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-primary/80 to-primary flex items-center justify-center shrink-0">
          <span className="text-[10px] font-medium text-primary-foreground">U</span>
        </div>
      )}
    </div>
  );
}

interface WorkflowChatPanelProps {
  workflowId?: string;
  workflowName?: string;
  nodes: Node[];
  edges: Edge[];
  isOpen?: boolean;
  onClose?: () => void;
  position?: 'left' | 'right';
  /** When true, renders as full-page component without ReactFlow Panel wrapper */
  standalone?: boolean;
}

export function WorkflowChatPanel({
  workflowId,
  workflowName,
  nodes,
  edges,
  isOpen: controlledOpen,
  onClose,
  position = 'right',
  standalone = false,
}: WorkflowChatPanelProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<WorkflowMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'chat' | 'trace'>('chat');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [executionTrace, setExecutionTrace] = useState<ExecutionTraceStep[]>([]);
  const [currentStepId, setCurrentStepId] = useState<string | undefined>();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Use controlled or uncontrolled open state
  const panelOpen = standalone ? true : (controlledOpen !== undefined ? controlledOpen : isOpen);
  const handleClose = onClose || (() => setIsOpen(false));
  const handleToggle = () => {
    if (controlledOpen === undefined && !standalone) {
      setIsOpen(!isOpen);
    }
  };

  // Store state
  const contextKey = workflowId ? `workflow:${workflowId}` : 'workflow:new';
  const { setStreaming, isStreaming } = useChatPanelStore();
  const { addJob, getActiveJobForContext } = useBackgroundJobStore();

  // Navigation guards
  const streamingActive = isStreaming(contextKey);
  const activeJob = getActiveJobForContext(contextKey);
  useNavigationGuard({
    shouldBlock: streamingActive,
    message: 'A response is being generated. Are you sure you want to leave? The response will be lost.',
  });
  useNavigationGuard({
    shouldBlock: !!activeJob,
    message: 'A workflow is currently executing. Are you sure you want to leave? The execution will continue in the background.',
  });

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSubmit = useCallback(async () => {
    if (!input.trim() || isLoading) return;

    const userMessage: LegacyMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: input.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);
    setStreaming(contextKey, true);
    setExecutionTrace([]);
    setActiveTab('trace'); // Switch to trace tab during execution

    try {
      // Use streaming execution endpoint directly for real-time tracing
      // This works for both saved and unsaved workflows
      const executeUrl = `${API_BASE_URL}/api/v1/workflows/execute-stream`;

      // CSRF token for state-changing POST request
      const csrfMatch = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
      const csrfToken = csrfMatch ? decodeURIComponent(csrfMatch[1]) : undefined;

      const response = await fetch(executeUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
        },
        credentials: 'include',
        body: JSON.stringify({
          workflow_id: workflowId || null,
          nodes: nodes.map(n => ({
            id: n.id,
            type: n.type || 'start',
            data: n.data,
            position: n.position,
          })),
          edges: edges.map(e => ({
            id: e.id,
            source: e.source,
            target: e.target,
            sourceHandle: e.sourceHandle,
            targetHandle: e.targetHandle,
          })),
          inputs: {
            query: userMessage.content,
            'sys.query': userMessage.content,
          },
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let finalOutput: string = '';
      const collectedTrace: ExecutionTraceStep[] = [];
      const nodeErrors: { nodeId: string; error: string }[] = [];

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value);
          const lines = chunk.split('\n');

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.substring(6));

                switch (event.type) {
                  case 'node_started':
                    setCurrentStepId(event.node_id);
                    break;
                  case 'node_complete': {
                    // Add to execution trace
                    const traceStep: ExecutionTraceStep = {
                      step_id: event.node_id,
                      node_id: event.node_id,
                      status: event.result.status === 'success' ? 'completed' : 'failed',
                      input: {},
                      output: event.result.output,
                      started_at: new Date().toISOString(),
                      completed_at: new Date().toISOString(),
                      duration_ms: event.result.elapsed_ms || 0,
                    };
                    collectedTrace.push(traceStep);
                    setExecutionTrace([...collectedTrace]);

                    // Track errors from failed nodes
                    if (event.result.status === 'failed' && event.result.error) {
                      nodeErrors.push({
                        nodeId: event.node_id,
                        error: event.result.error,
                      });
                    }

                    // Extract text from LLM output or result
                    const output = event.result.output;
                    if (output && typeof output === 'object') {
                      if (output.text) finalOutput = output.text;
                      else if (output.message) finalOutput = output.message;
                      else if (output.content) finalOutput = output.content;
                      else if (output.result && typeof output.result === 'string') finalOutput = output.result;
                    } else if (typeof output === 'string' && output.trim()) {
                      finalOutput = output;
                    }
                    break;
                  }
                  case 'complete':
                    // Workflow completed
                    break;
                  case 'error':
                    throw new Error(event.error || 'Workflow execution failed');
                }
              } catch (parseError) {
                // Ignore malformed JSON lines
                if (parseError instanceof Error && parseError.message !== 'Unexpected end of JSON input') {
                  console.warn('Failed to parse SSE event:', parseError);
                }
              }
            }
          }
        }
      }

      // Create assistant message with result or error summary
      let messageContent: string;
      if (finalOutput) {
        messageContent = finalOutput;
      } else if (nodeErrors.length > 0) {
        // Show node errors when no output was produced
        const errorSummary = nodeErrors
          .map(e => `**${e.nodeId}**: ${e.error}`)
          .join('\n');
        messageContent = `Workflow encountered errors:\n\n${errorSummary}\n\nCheck the Trace tab for details.`;
      } else {
        messageContent = 'Workflow completed but produced no output.';
      }

      const assistantMessage: WorkflowMessage = {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: messageContent,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, assistantMessage]);
      setIsLoading(false);
      setStreaming(contextKey, false);
      setCurrentStepId(undefined);
      setActiveTab('chat'); // Switch back to chat to show result
    } catch (err) {
      const errorMessage: WorkflowMessage = {
        id: `error-${Date.now()}`,
        role: 'assistant',
        content: err instanceof Error
          ? `Error: ${err.message}`
          : 'Sorry, there was an error executing the workflow. Please try again.',
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorMessage]);
      setIsLoading(false);
      setStreaming(contextKey, false);
      setCurrentStepId(undefined);
    }
  }, [input, isLoading, nodes, edges, contextKey, setStreaming]);

  const handleSuggestionClick = useCallback((suggestion: string) => {
    setInput(suggestion);
  }, []);

  // Shared chat content component
  const chatContent = (
    <ChatErrorBoundary>
      <div
        className={cn(
          'bg-background flex flex-col overflow-hidden',
          standalone
            ? 'h-full w-full'
            : 'w-[420px] h-[calc(100vh-12rem)] border rounded-xl shadow-2xl shadow-black/10'
        )}
      >
        {/* Header - only show in overlay mode */}
        {!standalone && (
          <div className="flex items-center justify-between p-3 border-b bg-muted/30">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500/20 to-cyan-500/20 flex items-center justify-center">
                <Robot className="h-4 w-4 text-blue-600" weight="duotone" />
              </div>
              <div>
                <span className="font-medium text-sm">
                  {workflowName || 'Test Workflow'}
                </span>
                <p className="text-[10px] text-muted-foreground">
                  {workflowId ? 'Saved workflow' : 'Unsaved workflow'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1">
              {workflowId && (
                <BackgroundJobIndicator contextKey={contextKey} showPopover={true} />
              )}
              {workflowId && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => setHistoryOpen(true)}
                  title="Chat History"
                >
                  <ClockCounterClockwise className="h-4 w-4" />
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={handleClose}
                title="Close"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}

        {/* Tabs */}
        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as 'chat' | 'trace')}
          className="flex-1 flex flex-col min-h-0"
        >
          <TabsList className="mx-3 mt-2 grid w-auto grid-cols-2 h-9">
            <TabsTrigger value="chat" className="text-xs gap-1.5">
              <ChatCircleDots className="h-3.5 w-3.5" weight="duotone" />
              Chat
            </TabsTrigger>
            <TabsTrigger value="trace" className="text-xs gap-1.5">
              <Lightning className="h-3.5 w-3.5" weight="duotone" />
              Trace
              {executionTrace.length > 0 && (
                <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">
                  {executionTrace.length}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="chat" className="flex-1 flex flex-col m-0 mt-2 min-h-0">
            {messages.length === 0 ? (
              <ChatEmptyStateCompact
                context="workflow"
                onSuggestionClick={handleSuggestionClick}
              />
            ) : (
              <ScrollArea className="flex-1 p-3" ref={scrollRef}>
                {/* Render messages with custom output handling */}
                <div className="space-y-4">
                  {messages.map((msg) => (
                    <WorkflowMessageItem key={msg.id} message={msg} />
                  ))}
                </div>
                {isLoading && (
                  <ChatLoadingIndicator
                    message="Running workflow..."
                    variant="thinking"
                    className="mt-3"
                    compact
                  />
                )}
              </ScrollArea>
            )}

            {/* Input */}
            <ChatInputField
              value={input}
              onChange={setInput}
              onSubmit={handleSubmit}
              isLoading={isLoading}
              placeholder="Test your workflow..."
              compact
            />
          </TabsContent>

          <TabsContent value="trace" className="flex-1 m-0 min-h-0">
            <ExecutionTrace
              steps={executionTrace}
              currentStepId={currentStepId}
              contextName={workflowName || 'workflow'}
              compact
            />
          </TabsContent>
        </Tabs>
      </div>
    </ChatErrorBoundary>
  );

  // Standalone mode - render full-page chat without Panel wrapper
  if (standalone) {
    return (
      <>
        {chatContent}
        {/* Chat History Dialog */}
        {workflowId && (
          <ChatHistory
            contextType="workflow"
            contextId={workflowId}
            contextName={workflowName || 'Workflow'}
            open={historyOpen}
            onOpenChange={setHistoryOpen}
          />
        )}
      </>
    );
  }

  // Overlay mode - requires Panel from @xyflow/react (must be inside ReactFlowProvider)
  if (!Panel) {
    // Fallback if Panel is not available - render without positioning
    return chatContent;
  }

  // Toggle button when panel is closed (overlay mode only)
  if (!panelOpen) {
    return (
      <Panel position={position === 'right' ? 'top-right' : 'top-left'}>
        <Button
          onClick={handleToggle}
          className="flex items-center gap-2 rounded-xl shadow-lg"
          variant="secondary"
        >
          <ChatCircleDots className="h-4 w-4" weight="duotone" />
          Test Workflow
          {activeJob && (
            <Badge variant="default" className="ml-1">
              Running
            </Badge>
          )}
        </Button>
      </Panel>
    );
  }

  return (
    <>
      {/* Panel Overlay */}
      <Panel
        position={position === 'right' ? 'top-right' : 'top-left'}
        className="m-0"
      >
        {chatContent}
      </Panel>

      {/* Chat History Dialog */}
      {workflowId && (
        <ChatHistory
          contextType="workflow"
          contextId={workflowId}
          contextName={workflowName || 'Workflow'}
          open={historyOpen}
          onOpenChange={setHistoryOpen}
        />
      )}
    </>
  );
}
