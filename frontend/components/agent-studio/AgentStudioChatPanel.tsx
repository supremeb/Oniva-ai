'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { TooltipProvider } from '@/components/ui/tooltip';
import {
  ChatCircleDots,
  Trash,
  TestTube,
} from '@phosphor-icons/react';
import {
  createChatSession,
  sendMessageStream,
  submitFeedback,
  type ChatSessionResponse,
} from '@/lib/api/agent-studio';
import { toast } from 'sonner';
import { ChatMessageList, type LegacyMessage, type ChatMessageData } from '@/components/chat/shared/ChatMessage';
import { GenerativeO } from '@/components/ui/GenerativeO';
import { useStreamingParts } from '@/hooks/useStreamingParts';
import { ConversationInput } from '@/components/chat/inputs/ConversationInput';
import { WebSearchPopover } from '@/components/chat/WebSearchPopover';
import { KnowledgePopover } from '@/components/chat/KnowledgePopover';
import { modelSupportsThinking } from '@/lib/models';
import type { Skill } from '@/lib/api/skills';

interface LocalMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  feedback?: 'positive' | 'negative' | null;
  testCaseId?: string | null;
  isStreaming?: boolean;
  richParts?: import('@/lib/models').MessagePart[];
}

interface AgentStudioChatPanelProps {
  agentId: string;
  agentName: string;
  versionId?: string;
  systemPrompt?: string;
  model?: string;
  hasUnsavedConfig?: boolean;
  onTestCaseCreated?: () => void;
}

export function AgentStudioChatPanel({
  agentId,
  agentName,
  versionId,
  systemPrompt,
  model = 'gpt-4o',
  hasUnsavedConfig = false,
  onTestCaseCreated,
}: AgentStudioChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // State
  const [session, setSession] = useState<ChatSessionResponse | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isInitializing, setIsInitializing] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedbackPending, setFeedbackPending] = useState<string | null>(null);

  // ConversationInput toolbar state (visual parity with main chat)
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [deepWorkMode, setDeepWorkMode] = useState(false);
  const [selectedSkills, setSelectedSkills] = useState<Skill[]>([]);
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [slashQuery] = useState('');
  const [activeConnectorIds] = useState<string[]>([]);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [activeKnowledgeBaseIds, setActiveKnowledgeBaseIds] = useState<string[]>([]);

  const thinkingSupported = modelSupportsThinking(model);

  // Streaming state via useStreamingParts hook
  const streaming = useStreamingParts();
  const streamingMsgIdRef = useRef<string | null>(null);

  // Initialize session
  useEffect(() => {
    const initSession = async () => {
      try {
        const newSession = await createChatSession(agentId, {
          version_id: versionId,
        });
        setSession(newSession);

        if (newSession.messages && newSession.messages.length > 0) {
          setMessages(
            newSession.messages.map((msg) => ({
              id: msg.id,
              role: msg.role,
              content: msg.content,
              feedback: msg.feedback,
              testCaseId: msg.test_case_id,
            }))
          );
        }
      } catch (e) {
        console.error('Failed to create session:', e);
        setSession({
          id: `local-${Date.now()}`,
          agent_id: agentId,
          agent_version_id: versionId,
          tenant_id: '',
          user_id: '',
          status: 'active',
          messages: [],
          total_messages: 0,
          positive_feedback_count: 0,
          negative_feedback_count: 0,
          total_tokens_used: 0,
          total_latency_ms: 0,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        });
      } finally {
        setIsInitializing(false);
      }
    };

    initSession();
  }, [agentId, versionId]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streaming.richParts]);

  // Handle message submit
  const handleSubmit = useCallback(
    () => {
      if (!input.trim() || isLoading || !session) return;

      const userMessage = input.trim();
      setInput('');
      setIsLoading(true);
      setError(null);

      // Add user message
      const userMsgId = `user-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        { id: userMsgId, role: 'user', content: userMessage },
      ]);

      // Add placeholder for assistant
      const assistantMsgId = `assistant-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        { id: assistantMsgId, role: 'assistant', content: '', isStreaming: true, richParts: [] },
      ]);
      streamingMsgIdRef.current = assistantMsgId;
      streaming.reset();

      // Stream response — adapt useStreamingParts handlers to EnhancedStreamCallbacks
      const abort = sendMessageStream(
        agentId,
        session.id,
        { content: userMessage, model, system_prompt: systemPrompt, enhancedMode: true },
        {
          onToken: (token) => {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantMsgId
                  ? { ...msg, content: msg.content + token }
                  : msg
              )
            );
            streaming.handlers.onContentDelta(token);
          },
          onToolCallStart: (event) => {
            streaming.handlers.onToolCallStart(
              event.toolName,
              event.toolCallId,
              undefined,
              undefined,
              event.args,
            );
          },
          onToolCallComplete: (event) => {
            streaming.handlers.onToolCallComplete({
              toolCallId: event.toolCallId,
              tool: event.toolName,
              success: event.status === 'success',
              result: event.result,
              error: event.error,
              durationMs: event.durationMs,
            });
          },
          onReasoning: (content) => {
            streaming.handlers.onReasoningDelta(content);
          },
          onComplete: (messageId, fullResponse) => {
            streaming.finalize();
            const snapshot = streaming.getSnapshot();
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantMsgId
                  ? {
                      ...msg,
                      id: messageId || assistantMsgId,
                      content: fullResponse,
                      isStreaming: false,
                      richParts: snapshot.richParts,
                    }
                  : msg
              )
            );
            streamingMsgIdRef.current = null;
            setIsLoading(false);
          },
          onError: (err) => {
            setError(err);
            setMessages((prev) => prev.filter((msg) => msg.id !== assistantMsgId));
            streamingMsgIdRef.current = null;
            streaming.reset();
            setIsLoading(false);
          },
        }
      );

      abortRef.current = abort;
    },
    [input, isLoading, session, agentId, model, systemPrompt, streaming]
  );

  // Handle feedback
  const handleFeedback = useCallback(
    async (messageId: string, _messageIndex: number, type: 'positive' | 'negative') => {
      if (!session || feedbackPending) return;

      setFeedbackPending(messageId);

      try {
        const result = await submitFeedback(agentId, session.id, messageId, {
          feedback: type,
          create_test_case: true,
        });

        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === messageId
              ? { ...msg, feedback: type, testCaseId: result.test_case_id }
              : msg
          )
        );

        if (result.test_case_created) {
          toast('Test Case Created', {
            description: type === 'positive'
              ? 'This response was saved as a positive example'
              : 'This response was saved as a negative example',
          });
          onTestCaseCreated?.();
        }
      } catch {
        toast.error('Error', { description: 'Failed to submit feedback' });
      } finally {
        setFeedbackPending(null);
      }
    },
    [session, agentId, feedbackPending, onTestCaseCreated]
  );

  // Handle stop
  const handleStop = useCallback(() => {
    abortRef.current?.();
    setIsLoading(false);
    streamingMsgIdRef.current = null;
  }, []);

  // Handle clear
  const handleClear = () => {
    setMessages([]);
    setInput('');
    setError(null);
    streaming.reset();
    streamingMsgIdRef.current = null;
  };

  // Toolbar callbacks
  const toggleThinking = useCallback(() => setThinkingEnabled(v => !v), []);
  const toggleDeepWork = useCallback(() => setDeepWorkMode(v => !v), []);
  const handleRemoveSkill = useCallback((skillId: string) => {
    setSelectedSkills(prev => prev.filter(s => s.id !== skillId));
  }, []);
  const handleSkillSelect = useCallback((skill: Skill) => {
    setSelectedSkills(prev =>
      prev.some(s => s.id === skill.id) ? prev : [...prev, skill]
    );
    setSkillPickerOpen(false);
  }, []);
  const handleToggleConnector = useCallback((_connectorId: string) => {
    // Connectors are configured at agent level in Agent Studio
  }, []);
  const handleToggleWebSearch = useCallback(() => setWebSearchEnabled(v => !v), []);
  const handleToggleKnowledgeBase = useCallback((kbId: string) => {
    setActiveKnowledgeBaseIds(prev =>
      prev.includes(kbId) ? prev.filter(id => id !== kbId) : [...prev, kbId]
    );
  }, []);

  // Source control popovers — same as ChatInterface
  const webSearchPopover = (
    <WebSearchPopover
      webSearchEnabled={webSearchEnabled}
      specificSites={[]}
      onToggleWebSearch={handleToggleWebSearch}
      onAddSite={() => {}}
      onRemoveSite={() => {}}
    />
  );

  const knowledgePopover = (
    <KnowledgePopover
      activeKnowledgeBaseIds={activeKnowledgeBaseIds}
      onToggleKnowledgeBase={handleToggleKnowledgeBase}
    />
  );

  // Convert LocalMessage[] to ChatMessageData[] for ChatMessageList
  const chatMessages: ChatMessageData[] = messages.map((msg) => ({
    id: msg.id,
    role: msg.role,
    content: msg.content,
    richParts: msg.richParts,
  } as LegacyMessage));

  const hasMessages = messages.length > 0;

  return (
    <TooltipProvider delayDuration={300}>
    <div className="flex flex-col h-full bg-[#f7f7f7]">
      {/* Slim top bar: draft indicator + clear */}
      {(hasUnsavedConfig || hasMessages) && (
        <div className="flex items-center justify-between px-5 py-2 shrink-0">
          <div className="flex items-center gap-2">
            {hasUnsavedConfig && (
              <>
                <Badge variant="outline" className="text-xs bg-amber-100 text-amber-800 border-amber-300">
                  DRAFT
                </Badge>
                <span className="text-xs text-amber-700">
                  Testing unsaved configuration
                </span>
              </>
            )}
          </div>
          {hasMessages && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClear}
              title="Clear Chat"
              disabled={isLoading}
              className="h-7 text-xs text-muted-foreground gap-1"
            >
              <Trash className="h-3.5 w-3.5" />
              Clear
            </Button>
          )}
        </div>
      )}

      {/* Messages — centered like main chat */}
      <ScrollArea className="flex-1" ref={scrollRef}>
        {!hasMessages && !isLoading && !isInitializing ? (
          <div className="flex flex-col items-center justify-center h-full text-center text-muted-foreground py-20">
            <ChatCircleDots className="h-12 w-12 mb-4 opacity-50" />
            <p className="font-medium text-lg">Test {agentName}</p>
            <p className="text-sm mt-1 max-w-[280px]">
              Send messages to test your agent and provide feedback to build your test suite
            </p>
          </div>
        ) : (
          <div className="px-6 py-6 max-w-[720px] mx-auto w-full">
            <ChatMessageList
              messages={chatMessages}
              renderMarkdown={true}
              showAvatars={true}
              streamingMessageId={streamingMsgIdRef.current || undefined}
              streamingRichParts={streaming.richParts.length > 0 ? streaming.richParts : undefined}
              toolCallMeta={streaming.toolCallMeta.size > 0 ? streaming.toolCallMeta : undefined}
              onFeedback={handleFeedback}
            />

            {/* Test case badges */}
            {messages.map((message) => (
              message.role === 'assistant' && message.testCaseId && (
                <div key={`tc-${message.id}`} className="flex items-center gap-1 ml-10 mt-1">
                  <Badge variant="outline" className="text-[10px] py-0 h-4">
                    <TestTube className="h-2.5 w-2.5 mr-0.5" />
                    Test case
                  </Badge>
                </div>
              )
            ))}

            {isLoading && messages[messages.length - 1]?.role !== 'assistant' && (
              <div className="flex items-center gap-2 py-2">
                <GenerativeO size={24} speed={1} animating={true} />
                <span className="text-sm text-muted-foreground">Thinking...</span>
              </div>
            )}

            {error && (
              <div className="mt-4 px-4 py-3 bg-destructive/10 text-destructive text-sm rounded-lg">
                {error}
              </div>
            )}
          </div>
        )}
      </ScrollArea>

      {/* Input — exact same ConversationInput as main chat */}
      <div className="px-6 pb-5 pt-3 bg-[#f7f7f7]">
        <ConversationInput
          value={input}
          onChange={setInput}
          onSubmit={handleSubmit}
          isLoading={isLoading}
          isStreaming={isLoading}
          onStop={handleStop}
          deepWorkMode={deepWorkMode}
          onDeepWorkToggle={toggleDeepWork}
          thinkingEnabled={thinkingEnabled}
          onThinkingToggle={toggleThinking}
          thinkingSupported={thinkingSupported}
          inputRef={inputRef}
          selectedSkills={selectedSkills}
          onRemoveSkill={handleRemoveSkill}
          sessionId={session?.id ?? null}
          activeConnectorIds={activeConnectorIds}
          onToggleConnector={handleToggleConnector}
          skillPickerOpen={skillPickerOpen}
          onSkillPickerOpenChange={setSkillPickerOpen}
          slashQuery={slashQuery}
          onSkillSelect={handleSkillSelect}
          webSearchPopover={webSearchPopover}
          knowledgePopover={knowledgePopover}
          modelName={model}
        />
      </div>
    </div>
    </TooltipProvider>
  );
}
