'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Copy, Check, CaretDown, CaretRight, File } from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { StreamingCursor } from './StreamingCursor';
import { animations, transitions } from '@/lib/animation-utils';

export interface StreamingCodeProps {
  /** Code content */
  content: string;
  /** Programming language */
  language?: string;
  /** Filename for display */
  filename?: string;
  /** Whether currently streaming */
  isStreaming?: boolean;
  /** Whether code block is collapsible */
  collapsible?: boolean;
  /** Default collapsed state */
  defaultCollapsed?: boolean;
  /** Show line numbers */
  showLineNumbers?: boolean;
  /** Maximum height before scrolling */
  maxHeight?: number | string;
  /** Additional CSS classes */
  className?: string;
  /** Callback when copy is clicked */
  onCopy?: (content: string) => void;
}

// Language display names
const languageDisplayNames: Record<string, string> = {
  js: 'JavaScript',
  javascript: 'JavaScript',
  ts: 'TypeScript',
  typescript: 'TypeScript',
  tsx: 'TSX',
  jsx: 'JSX',
  py: 'Python',
  python: 'Python',
  rb: 'Ruby',
  ruby: 'Ruby',
  go: 'Go',
  rust: 'Rust',
  java: 'Java',
  kotlin: 'Kotlin',
  swift: 'Swift',
  cpp: 'C++',
  c: 'C',
  cs: 'C#',
  csharp: 'C#',
  php: 'PHP',
  sql: 'SQL',
  bash: 'Bash',
  sh: 'Shell',
  shell: 'Shell',
  json: 'JSON',
  yaml: 'YAML',
  yml: 'YAML',
  xml: 'XML',
  html: 'HTML',
  css: 'CSS',
  scss: 'SCSS',
  md: 'Markdown',
  markdown: 'Markdown',
};

/**
 * StreamingCode - Code block with syntax highlighting and copy functionality
 *
 * Features:
 * - Language badge
 * - Copy button with success feedback
 * - Line numbers (optional)
 * - Collapsible (optional)
 * - Streaming cursor while generating
 */
export const StreamingCode = memo(function StreamingCode({
  content,
  language = 'text',
  filename,
  isStreaming = false,
  collapsible = false,
  defaultCollapsed = false,
  showLineNumbers = false,
  maxHeight = 400,
  className,
  onCopy,
}: StreamingCodeProps) {
  const [copied, setCopied] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      onCopy?.(content);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [content, onCopy]);

  const toggleCollapse = useCallback(() => {
    setIsCollapsed((prev) => !prev);
  }, []);

  const lines = content.split('\n');
  const displayLanguage = languageDisplayNames[language.toLowerCase()] || language.toUpperCase();

  return (
    <FadeSlide className={cn('rounded-lg border border-border bg-muted/30 overflow-hidden', className)}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-muted/50">
        <div className="flex items-center gap-2">
          {/* Collapse toggle */}
          {collapsible && (
            <button
              onClick={toggleCollapse}
              className={cn(
                'p-0.5 rounded hover:bg-muted',
                transitions.colors
              )}
              aria-expanded={!isCollapsed}
            >
              {isCollapsed ? (
                <CaretRight className="h-3.5 w-3.5 text-muted-foreground" />
              ) : (
                <CaretDown className="h-3.5 w-3.5 text-muted-foreground" />
              )}
            </button>
          )}

          {/* Filename or language badge */}
          {filename ? (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <File className="h-3.5 w-3.5" weight="regular" />
              <span className="font-medium">{filename}</span>
            </div>
          ) : (
            <span className="text-xs font-medium text-muted-foreground px-1.5 py-0.5 rounded bg-muted">
              {displayLanguage}
            </span>
          )}
        </div>

        {/* Copy button */}
        <button
          onClick={handleCopy}
          className={cn(
            'flex items-center gap-1 px-2 py-1 rounded text-xs',
            'text-muted-foreground hover:text-foreground',
            'hover:bg-muted',
            animations.press,
            transitions.colors
          )}
          title="Copy code"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5 text-green-500" weight="bold" />
              <span className="text-green-500">Copied!</span>
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" weight="regular" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>

      {/* Code content */}
      {!isCollapsed && (
        <div
          className="overflow-auto"
          style={{ maxHeight: typeof maxHeight === 'number' ? `${maxHeight}px` : maxHeight }}
        >
          <pre className="p-3 text-sm font-mono leading-relaxed">
            {showLineNumbers ? (
              <code className="grid">
                {lines.map((line, index) => (
                  <div key={index} className="flex">
                    <span className="select-none w-8 pr-3 text-right text-muted-foreground/50 shrink-0">
                      {index + 1}
                    </span>
                    <span className="flex-1 whitespace-pre-wrap break-all">
                      {line || ' '}
                    </span>
                  </div>
                ))}
                {isStreaming && (
                  <div className="flex">
                    <span className="select-none w-8 pr-3 text-right text-muted-foreground/50 shrink-0" />
                    <StreamingCursor variant="block" size="sm" />
                  </div>
                )}
              </code>
            ) : (
              <code className="whitespace-pre-wrap break-all">
                {content}
                {isStreaming && <StreamingCursor variant="block" size="sm" className="ml-0.5" />}
              </code>
            )}
          </pre>
        </div>
      )}

      {/* Collapsed indicator */}
      {isCollapsed && (
        <div className="px-3 py-2 text-xs text-muted-foreground">
          {lines.length} lines
        </div>
      )}
    </FadeSlide>
  );
});

export type { StreamingCodeProps as StreamingCodeComponentProps };
