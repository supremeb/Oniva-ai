'use client';

import { cn } from '@/lib/utils';
import { Robot, User, Copy, Check, ThumbsUp, ThumbsDown, ArrowsClockwise, Brain, CaretDown, CaretRight } from '@phosphor-icons/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useState, useCallback, useEffect, useRef, memo, Fragment } from 'react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { GenerativeO } from '@/components/ui/GenerativeO';
import type { MessagePart, SourceItem } from '@/lib/models';
import type { ToolCallMeta } from '@/hooks/useStreamingParts';
import type { ArtifactModalData } from './data/FilePreview';
import { ChainOfThought } from './ChainOfThought';

/**
 * UIMessage type definition (replaces AI SDK import)
 * Matches AI SDK structure for backward compatibility
 */
interface UIMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  parts: Array<{ type: 'text'; text: string } | { type: string; [key: string]: unknown }>;
}

/**
 * Legacy message format for backward compatibility
 */
export interface LegacyMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    reasoning?: string;
    timestamp?: Date;
}

/**
 * Union type supporting both AI SDK UIMessage and legacy format
 */
export type ChatMessageData = UIMessage | LegacyMessage;

export interface ChatMessageProps {
    /** Message data - supports UIMessage (AI SDK) or LegacyMessage format */
    message: ChatMessageData;
    /** Render markdown content */
    renderMarkdown?: boolean;
    /** Show avatar */
    showAvatar?: boolean;
    /** Additional CSS classes for the container */
    className?: string;
    /** Additional CSS classes for the message bubble */
    bubbleClassName?: string;
    /** Compact mode for smaller chat panels */
    compact?: boolean;
    /** Whether this message is streaming */
    isStreaming?: boolean;
    /** Callback for regenerate action */
    onRegenerate?: () => void;
    /** Callback to persist feedback (thumbs up/down) to backend */
    onFeedback?: (rating: 'positive' | 'negative') => void;
}

/**
 * Check if message is a UIMessage (has parts array)
 */
function isUIMessage(message: ChatMessageData): message is UIMessage {
    return 'parts' in message && Array.isArray(message.parts);
}

/**
 * Extract text content from a message
 */
function getMessageContent(message: ChatMessageData): string {
    if (isUIMessage(message)) {
        return message.parts
            .filter((part) => part.type === 'text')
            .map((part) => (part as { type: 'text'; text: string }).text)
            .join('\n');
    }
    return message.content;
}

/**
 * Extract reasoning from a message if available
 */
function getMessageReasoning(message: ChatMessageData): string | undefined {
    if (!isUIMessage(message) && 'reasoning' in message) {
        return message.reasoning;
    }
    return undefined;
}

// Oniva dot logo - only shown during thinking/streaming with shimmer animation
const ThinkingAvatar = memo(function ThinkingAvatar({ compact, animating = true }: { compact?: boolean; animating?: boolean }) {
    const size = compact ? 24 : 28;
    return (
        <div className="shrink-0">
            <GenerativeO
                size={size}
                speed={1}
                animating={animating}
            />
        </div>
    );
});

// Message action button component - Onyx style
const ActionButton = memo(function ActionButton({
    icon: Icon,
    onClick,
    active,
    compact,
    title,
}: {
    icon: typeof Copy;
    onClick: () => void;
    active?: boolean;
    compact?: boolean;
    title?: string;
}) {
    return (
        <button
            onClick={onClick}
            title={title}
            className={cn(
                'flex items-center justify-center rounded-md transition-colors',
                compact ? 'h-6 w-6' : 'h-7 w-7',
                active
                    ? 'text-neutral-700 bg-neutral-100'
                    : 'text-neutral-400 hover:text-neutral-600 hover:bg-neutral-100'
            )}
        >
            <Icon
                className={cn(compact ? 'h-3.5 w-3.5' : 'h-4 w-4')}
                weight={active ? 'fill' : 'regular'}
            />
        </button>
    );
});

// Message actions (copy, thumbs up/down, regenerate) - Onyx style
export const MessageActions = memo(function MessageActions({
    content,
    compact,
    showRegenerate,
    onRegenerate,
    onFeedback,
}: {
    content: string;
    compact?: boolean;
    showRegenerate?: boolean;
    onRegenerate?: () => void;
    /** Callback to persist feedback to the backend. Rating is 'positive' or 'negative'. */
    onFeedback?: (rating: 'positive' | 'negative') => void;
}) {
    const [copied, setCopied] = useState(false);
    const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);

    const handleCopy = useCallback(async () => {
        await navigator.clipboard.writeText(content);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    }, [content]);

    const handleThumbsUp = useCallback(() => {
        const next = feedback === 'up' ? null : 'up';
        setFeedback(next);
        if (next === 'up' && onFeedback) onFeedback('positive');
    }, [feedback, onFeedback]);

    const handleThumbsDown = useCallback(() => {
        const next = feedback === 'down' ? null : 'down';
        setFeedback(next);
        if (next === 'down' && onFeedback) onFeedback('negative');
    }, [feedback, onFeedback]);

    return (
        <div className="flex items-center gap-0.5 mt-2 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
            <ActionButton
                icon={copied ? Check : Copy}
                onClick={handleCopy}
                compact={compact}
                title="Copy"
                active={copied}
            />
            <ActionButton
                icon={ThumbsUp}
                onClick={handleThumbsUp}
                compact={compact}
                title="Good response"
                active={feedback === 'up'}
            />
            <ActionButton
                icon={ThumbsDown}
                onClick={handleThumbsDown}
                compact={compact}
                title="Bad response"
                active={feedback === 'down'}
            />
            {showRegenerate && onRegenerate && (
                <ActionButton
                    icon={ArrowsClockwise}
                    onClick={onRegenerate}
                    compact={compact}
                    title="Regenerate"
                />
            )}
        </div>
    );
});

// Streaming cursor animation
const StreamingCursor = memo(function StreamingCursor() {
    return (
        <span className="inline-block w-1.5 h-4 bg-neutral-400 ml-0.5 animate-pulse rounded-sm" />
    );
});

// Reasoning display component with streaming-aware collapsible UI
const MAX_REASONING_LENGTH = 5000;

const ReasoningDisplay = memo(function ReasoningDisplay({
    reasoning,
    compact,
    isStreaming,
}: {
    reasoning: string;
    compact?: boolean;
    isStreaming?: boolean;
}) {
    const [isOpen, setIsOpen] = useState(false);
    const [wasStreaming, setWasStreaming] = useState(false);
    const startTime = useRef<number | null>(null);
    const [duration, setDuration] = useState<number | null>(null);

    // Track thinking duration
    useEffect(() => {
        if (isStreaming && reasoning && !startTime.current) {
            startTime.current = Date.now();
        }
        if (!isStreaming && startTime.current) {
            setDuration(Math.round((Date.now() - startTime.current) / 1000));
            startTime.current = null;
        }
    }, [isStreaming, reasoning]);

    // Auto-expand while streaming, auto-collapse when done
    useEffect(() => {
        if (isStreaming && reasoning) {
            setIsOpen(true);
            setWasStreaming(true);
        } else if (wasStreaming && !isStreaming) {
            const timer = setTimeout(() => setIsOpen(false), 500);
            setWasStreaming(false);
            return () => clearTimeout(timer);
        }
    }, [isStreaming, reasoning, wasStreaming]);

    const truncated = reasoning.length > MAX_REASONING_LENGTH;
    const displayText = truncated ? reasoning.slice(0, MAX_REASONING_LENGTH) + '...' : reasoning;

    const triggerLabel = isStreaming
        ? 'Thinking...'
        : duration
            ? `Thought for ${duration}s`
            : 'View reasoning';

    return (
        <Collapsible open={isOpen} onOpenChange={setIsOpen}>
            <CollapsibleTrigger className={cn(
                "flex items-center gap-2 text-sm mb-2 transition-colors w-full",
                "text-neutral-500 hover:text-neutral-700",
            )}>
                <Brain
                    className={cn("h-4 w-4", isStreaming && "animate-pulse")}
                    weight={isStreaming ? "fill" : "duotone"}
                />
                <span className="flex-1 text-left">{triggerLabel}</span>
                {isOpen ? (
                    <CaretDown className="h-3 w-3" />
                ) : (
                    <CaretRight className="h-3 w-3" />
                )}
            </CollapsibleTrigger>
            <CollapsibleContent>
                <div className="py-2">
                    <pre className={cn(
                        "text-sm text-neutral-500 whitespace-pre-wrap font-sans leading-relaxed",
                        compact && "text-xs"
                    )}>
                        {displayText}
                        {isStreaming && <span className="inline-block w-1.5 h-3 bg-neutral-400 ml-0.5 animate-pulse rounded-sm" />}
                    </pre>
                    {truncated && !isStreaming && (
                        <button
                            className="text-xs text-neutral-500 hover:underline mt-2"
                            onClick={() => {
                                const el = document.querySelector('[data-chain-of-thought]');
                                if (el) {
                                    const trigger = el.querySelector('[data-radix-collapsible-trigger]') as HTMLElement;
                                    trigger?.click();
                                }
                            }}
                        >
                            Show full reasoning ({Math.round(reasoning.length / 1000)}k chars)
                        </button>
                    )}
                </div>
            </CollapsibleContent>
        </Collapsible>
    );
});

export const ChatMessage = memo(function ChatMessage({
    message,
    renderMarkdown = true,
    showAvatar = true,
    className,
    bubbleClassName,
    compact = false,
    isStreaming = false,
    onRegenerate,
    onFeedback,
}: ChatMessageProps) {
    const isUser = message.role === 'user';
    const content = getMessageContent(message);
    const reasoning = getMessageReasoning(message);

    const messageContent = renderMarkdown && !isUser ? (
        <div
            className={cn(
                'prose prose-sm dark:prose-invert max-w-none',
                'prose-p:leading-relaxed prose-p:my-1.5 prose-p:text-neutral-800',
                'prose-pre:bg-neutral-100 prose-pre:rounded-lg prose-pre:my-3 prose-pre:border prose-pre:border-neutral-200',
                'prose-code:bg-neutral-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-code:font-mono prose-code:text-neutral-700',
                'prose-ul:my-2 prose-ol:my-2',
                'prose-li:my-0.5 prose-li:text-neutral-700',
                'prose-headings:mt-4 prose-headings:mb-2 prose-headings:text-neutral-900',
                'prose-strong:text-neutral-900',
                compact && 'prose-sm'
            )}
        >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            {isStreaming && <StreamingCursor />}
        </div>
    ) : (
        <p className={cn(
            'whitespace-pre-wrap leading-relaxed',
            compact ? 'text-sm' : 'text-[15px]'
        )}>
            {content}
            {isStreaming && <StreamingCursor />}
        </p>
    );

    // User message - Onyx style: dark rounded pill, right aligned
    if (isUser) {
        return (
            <div className={cn('flex justify-end group', className)}>
                <div
                    className={cn(
                        'rounded-2xl px-4 py-2.5',
                        'bg-neutral-800 text-white',
                        'max-w-[75%]',
                        'shadow-sm',
                        bubbleClassName
                    )}
                >
                    {messageContent}
                </div>
            </div>
        );
    }

    // Assistant message - Onyx style: left aligned, Oniva dot logo only during thinking
    return (
        <div className={cn('flex gap-3 justify-start group', className)}>
            {/* Only show Oniva dot logo during streaming/thinking state */}
            {showAvatar && isStreaming && <ThinkingAvatar compact={compact} animating={true} />}
            <div className="flex flex-col max-w-[85%]">
                {/* Message content */}
                <div className={cn('text-neutral-800', bubbleClassName)}>
                    {messageContent}
                </div>

                {/* Reasoning display (collapsible, streaming-aware) — after response */}
                {reasoning && (
                    <ReasoningDisplay reasoning={reasoning} compact={compact} isStreaming={isStreaming} />
                )}

                {/* Actions below the message */}
                {!isStreaming && (
                    <MessageActions
                        content={content}
                        compact={compact}
                        showRegenerate={true}
                        onRegenerate={onRegenerate}
                        onFeedback={onFeedback}
                    />
                )}
            </div>
        </div>
    );
});

/**
 * Render a list of messages with proper spacing
 */
export interface ChatMessageListProps {
    messages: ChatMessageData[];
    renderMarkdown?: boolean;
    showAvatars?: boolean;
    compact?: boolean;
    className?: string;
    /** ID of the currently streaming message */
    streamingMessageId?: string;
    /** Rich structured parts for the streaming message (renders via MessageRenderer) */
    streamingRichParts?: MessagePart[];
    /** Enriched tool call metadata for Timeline + TimelineEvent */
    toolCallMeta?: Map<string, ToolCallMeta>;
    /** Aggregated sources for SourcesPill */
    sources?: SourceItem[];
    /** Callback for regenerate action */
    onRegenerate?: () => void;
    /** Callback to persist feedback. Receives message ID, index, and rating. */
    onFeedback?: (messageId: string, messageIndex: number, rating: 'positive' | 'negative') => void;
    /** Callback to open artifact modal */
    onOpenModal?: (data: ArtifactModalData) => void;
    /** Approve/reject handlers for approval parts (receive the part id). */
    approvalHandlers?: {
        onApprove?: (id: string) => void | Promise<void>;
        onReject?: (id: string) => void | Promise<void>;
        onQuestionAnswer?: (id: string, answer: { optionIds: string[]; otherValue?: string }) => void | Promise<void>;
    };
    /** Extra content under a message (e.g. a per-turn cost caption). */
    renderMessageFooter?: (message: ChatMessageData) => React.ReactNode;
}

/**
 * Extract concatenated text from MessagePart[] for clipboard copy.
 * Only includes 'text' type parts (excludes thinking/tool calls).
 */
function getTextContent(parts: MessagePart[]): string {
    return parts
        .filter((p) => p.type === 'text')
        .map((p) => (p as any).content || '')
        .join('\n')
        .trim();
}

export const ChatMessageList = memo(function ChatMessageList({
    messages,
    renderMarkdown = true,
    showAvatars = true,
    compact = false,
    className,
    streamingMessageId,
    streamingRichParts,
    toolCallMeta,
    sources,
    onRegenerate,
    onFeedback,
    onOpenModal,
    approvalHandlers,
    renderMessageFooter,
}: ChatMessageListProps) {
    if (messages.length === 0) {
        return null;
    }

    return (
        <div className={cn('space-y-6', compact && 'space-y-4', className)}>
            {messages.map((message, index) => {
                const footer = renderMessageFooter?.(message);
                const wrap = (el: React.ReactElement) =>
                    footer ? (
                        <Fragment key={message.id}>
                            {el}
                            <div className={cn('-mt-3', showAvatars && 'pl-10')}>{footer}</div>
                        </Fragment>
                    ) : (
                        el
                    );
                const isStreamingMsg = message.id === streamingMessageId;

                // Determine rich parts: streaming message uses live parts, restored messages use persisted
                const richParts = isStreamingMsg
                    ? streamingRichParts
                    : ('richParts' in message && Array.isArray((message as any).richParts))
                        ? (message as any).richParts as MessagePart[]
                        : undefined;

                // Render via ChainOfThought when rich parts are available
                // Groups thinking + tool calls into one collapsible timeline,
                // renders response parts (text, tables, sources) below.
                if (message.role === 'assistant' && richParts && richParts.length > 0) {
                    return wrap(
                        <div key={message.id} className="flex gap-3 justify-start group">
                            {showAvatars && (
                                <div className="shrink-0">
                                    <GenerativeO size={compact ? 24 : 28} speed={1} animating={isStreamingMsg} />
                                </div>
                            )}
                            <div className="flex flex-col w-full min-w-0">
                                <ChainOfThought
                                    parts={richParts}
                                    compact={compact}
                                    animated={isStreamingMsg}
                                    toolCallMeta={isStreamingMsg ? toolCallMeta : undefined}
                                    sources={isStreamingMsg ? sources : (message as any).sources}
                                    handlers={onOpenModal || approvalHandlers ? { ...(onOpenModal ? { onOpenModal } : {}), ...approvalHandlers } : undefined}
                                />
                                {!isStreamingMsg && (
                                    <MessageActions
                                        content={getTextContent(richParts)}
                                        compact={compact}
                                        showRegenerate={false}
                                        onFeedback={
                                            onFeedback
                                                ? (rating) => onFeedback(message.id, index, rating)
                                                : undefined
                                        }
                                    />
                                )}
                            </div>
                        </div>
                    );
                }

                return wrap(
                    <ChatMessage
                        key={message.id}
                        message={message}
                        renderMarkdown={renderMarkdown}
                        showAvatar={showAvatars}
                        compact={compact}
                        isStreaming={isStreamingMsg}
                        onRegenerate={onRegenerate}
                        onFeedback={
                            onFeedback && message.role === 'assistant'
                                ? (rating) => onFeedback(message.id, index, rating)
                                : undefined
                        }
                    />
                );
            })}
        </div>
    );
});
