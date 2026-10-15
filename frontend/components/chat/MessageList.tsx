'use client';

import type { ReactNode, RefObject } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
    ChatMessageList,
    ChatLoadingIndicator,
    ChatError,
} from './shared';
import { ToolCallCardCompact } from './shared/tools/ToolCallCard';
import type { ArtifactModalData } from './shared/data/FilePreview';
import { DeepWorkCard } from '@/components/deep-work/DeepWorkCard';
import { WorkflowProposal } from './WorkflowProposal';
import { WorkflowProgress } from './WorkflowProgress';
import type { MessagePart } from '@/lib/models';
import type { useStreamingParts } from '@/hooks/useStreamingParts';

export interface ActiveToolCall {
    id: string;
    toolName: string;
    status: 'running' | 'success' | 'error';
}

export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    reasoning?: string;
    /** Rich structured parts for MessageRenderer (populated during streaming) */
    richParts?: MessagePart[];
}

type StreamingParts = ReturnType<typeof useStreamingParts>;

export interface MessageListProps {
    scrollRef: RefObject<HTMLDivElement | null>;
    viewportRef: RefObject<HTMLDivElement | null>;
    messages: ChatMessage[];
    streamingMessageId: string | undefined;
    streamingRichParts: StreamingParts['richParts'];
    streamingToolCallMeta: StreamingParts['toolCallMeta'];
    richSources: StreamingParts['sources'];
    onOpenModal: (data: ArtifactModalData) => void;
    activeToolCalls: ActiveToolCall[];
    workflowPreview: StreamingParts['workflowPreview'];
    workflowProgress: StreamingParts['workflowProgress'];
    workflowResult: StreamingParts['workflowResult'];
    workflowDismissed: boolean;
    isWorkflowExecuting: boolean;
    onWorkflowExecute: (workflowId: string, inputs: Record<string, any>) => void | Promise<void>;
    onWorkflowCancel: () => void;
    isDeepWorkForThisChat: boolean;
    isLoading: boolean;
    isStreaming: boolean;
    streamStale: boolean;
    error: string | null;
    onRetry: () => void;
    onDismissError: () => void;
}

/** Scrollable transcript plus the in-flight status cards below it. */
export function MessageList({
    scrollRef,
    viewportRef,
    messages,
    streamingMessageId,
    streamingRichParts,
    streamingToolCallMeta,
    richSources,
    onOpenModal,
    activeToolCalls,
    workflowPreview,
    workflowProgress,
    workflowResult,
    workflowDismissed,
    isWorkflowExecuting,
    onWorkflowExecute,
    onWorkflowCancel,
    isDeepWorkForThisChat,
    isLoading,
    isStreaming,
    streamStale,
    error,
    onRetry,
    onDismissError,
}: MessageListProps): ReactNode {
    return (
        <ScrollArea className="flex-1" ref={scrollRef} viewportRef={viewportRef}>
            <div className="px-6 py-6 max-w-[720px] mx-auto w-full">
                <ChatMessageList messages={messages} renderMarkdown showAvatars streamingMessageId={streamingMessageId} streamingRichParts={streamingRichParts} toolCallMeta={streamingToolCallMeta} sources={richSources} onOpenModal={onOpenModal} />
                {activeToolCalls.length > 0 && !streamingRichParts.length && (
                    <div className="mt-3 space-y-2">
                        {activeToolCalls.map(tc => (
                            <ToolCallCardCompact key={tc.id} toolName={tc.toolName} status={tc.status} />
                        ))}
                    </div>
                )}
                {/* Workflow Phase 1: Proposal card (pending approval) */}
                {workflowPreview && !workflowDismissed && !workflowProgress.size && !workflowResult && (
                    <div className="mt-3">
                        <WorkflowProposal
                            preview={workflowPreview}
                            onExecute={onWorkflowExecute}
                            onCancel={onWorkflowCancel}
                            isExecuting={isWorkflowExecuting}
                        />
                    </div>
                )}
                {/* Workflow Phase 2: Progress card (executing or completed) */}
                {workflowPreview && (workflowProgress.size > 0 || workflowResult) && (
                    <div className="mt-3">
                        <WorkflowProgress
                            workflowName={workflowPreview.workflow_name}
                            steps={workflowPreview.steps}
                            progress={workflowProgress}
                            result={workflowResult}
                        />
                    </div>
                )}
                {isDeepWorkForThisChat && <DeepWorkCard />}
                {isLoading && !isStreaming && !streamingMessageId && <ChatLoadingIndicator message="Thinking..." variant="generative" className="mt-6" />}
                {streamStale && isStreaming && (
                    <div className="mt-3 text-sm text-amber-600 flex items-center gap-2">
                        <span className="inline-block w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                        Stream appears stale — waiting for server response...
                    </div>
                )}
                {error && <ChatError message={error} onRetry={onRetry} onDismiss={onDismissError} className="mt-6" />}
            </div>
        </ScrollArea>
    );
}
