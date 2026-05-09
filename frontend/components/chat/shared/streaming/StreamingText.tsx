'use client';

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/utils';
import { StreamingCursor } from './StreamingCursor';

export interface StreamingTextProps {
  /** Text content (can include markdown) */
  content: string;
  /** Whether currently streaming */
  isStreaming?: boolean;
  /** Whether to render markdown */
  renderMarkdown?: boolean;
  /** Compact mode for smaller displays */
  compact?: boolean;
  /** Additional CSS classes */
  className?: string;
}

/**
 * StreamingText - Renders streaming text with markdown support
 *
 * Provides:
 * - Markdown rendering with react-markdown
 * - Blinking cursor while streaming
 * - GFM support for tables, strikethrough, etc.
 */
export const StreamingText = memo(function StreamingText({
  content,
  isStreaming = false,
  renderMarkdown = true,
  compact = false,
  className,
}: StreamingTextProps) {
  // Plain text rendering (no markdown)
  if (!renderMarkdown) {
    return (
      <div className={cn('whitespace-pre-wrap', compact ? 'text-sm' : 'text-[15px]', className)}>
        {content}
        {isStreaming && <StreamingCursor />}
      </div>
    );
  }

  // Markdown rendering
  return (
    <div
      className={cn(
        'prose prose-neutral dark:prose-invert max-w-none',
        // Paragraph styling
        'prose-p:leading-relaxed prose-p:my-1.5 prose-p:text-foreground',
        // Code styling
        'prose-pre:bg-muted prose-pre:rounded-lg prose-pre:my-3 prose-pre:border prose-pre:border-border',
        'prose-code:bg-muted prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-code:font-mono prose-code:text-foreground',
        // List styling
        'prose-ul:my-2 prose-ol:my-2',
        'prose-li:my-0.5 prose-li:text-foreground',
        // Heading styling
        'prose-headings:mt-4 prose-headings:mb-2 prose-headings:text-foreground',
        'prose-strong:text-foreground',
        // Link styling
        'prose-a:text-primary prose-a:no-underline hover:prose-a:underline',
        // Table styling
        'prose-table:border prose-table:border-border prose-table:rounded-lg',
        'prose-th:bg-muted prose-th:px-3 prose-th:py-2 prose-th:text-left prose-th:font-medium',
        'prose-td:px-3 prose-td:py-2 prose-td:border-t prose-td:border-border',
        // Size variants
        compact ? 'prose-sm' : 'prose-base',
        className
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Custom paragraph to handle streaming cursor
          p: ({ children, ...props }) => (
            <p {...props}>
              {children}
              {/* Cursor only on the last paragraph during streaming */}
            </p>
          ),
          // Custom code block styling
          code: ({ className: codeClassName, children, ...props }) => {
            const isInline = !codeClassName;
            if (isInline) {
              return (
                <code
                  className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono"
                  {...props}
                >
                  {children}
                </code>
              );
            }
            return (
              <code className={codeClassName} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
      {isStreaming && <StreamingCursor className="ml-0.5" />}
    </div>
  );
});

export type { StreamingTextProps as StreamingTextComponentProps };
