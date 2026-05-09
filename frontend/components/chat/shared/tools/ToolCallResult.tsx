'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { CaretDown, CaretRight, Copy, Check, Terminal } from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface ToolCallResultProps {
  /** Result content (can be string, object, or any JSON-serializable value) */
  result: unknown;
  /** Result format */
  format?: 'json' | 'text' | 'terminal';
  /** Whether result is collapsible */
  collapsible?: boolean;
  /** Default collapsed state */
  defaultCollapsed?: boolean;
  /** Maximum height before scrolling */
  maxHeight?: number;
  /** Show copy button */
  copyable?: boolean;
  /** Additional CSS classes */
  className?: string;
}

/**
 * ToolCallResult - Display tool execution results
 *
 * Inspired by Tool UI Terminal component.
 * Features:
 * - JSON/text formatting
 * - Collapsible content
 * - Copy functionality
 * - Terminal-style display
 */
export const ToolCallResult = memo(function ToolCallResult({
  result,
  format = 'json',
  collapsible = true,
  defaultCollapsed = true,
  maxHeight = 300,
  copyable = true,
  className,
}: ToolCallResultProps) {
  const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);
  const [copied, setCopied] = useState(false);

  // Format result for display
  const formattedResult = (() => {
    if (result === null) return 'null';
    if (result === undefined) return 'undefined';
    if (typeof result === 'string') return result;
    try {
      return JSON.stringify(result, null, 2);
    } catch {
      return String(result);
    }
  })();

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(formattedResult);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [formattedResult]);

  const toggleCollapse = useCallback(() => {
    setIsCollapsed((prev) => !prev);
  }, []);

  const lineCount = formattedResult.split('\n').length;

  return (
    <FadeSlide
      className={cn(
        'rounded-lg border border-border bg-neutral-950 overflow-hidden',
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-neutral-900 border-b border-neutral-800">
        <div className="flex items-center gap-2">
          {/* Collapse toggle */}
          {collapsible && (
            <button
              onClick={toggleCollapse}
              className={cn(
                'p-0.5 rounded hover:bg-neutral-800',
                transitions.colors
              )}
              aria-expanded={!isCollapsed}
            >
              {isCollapsed ? (
                <CaretRight className="h-3.5 w-3.5 text-neutral-400" />
              ) : (
                <CaretDown className="h-3.5 w-3.5 text-neutral-400" />
              )}
            </button>
          )}

          {/* Terminal icon and label */}
          <div className="flex items-center gap-1.5 text-xs text-neutral-400">
            <Terminal className="h-3.5 w-3.5" weight="regular" />
            <span>Output</span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1">
          {/* Line count */}
          <span className="text-xs text-neutral-500 mr-2">
            {lineCount} lines
          </span>

          {/* Copy button */}
          {copyable && (
            <button
              onClick={handleCopy}
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded text-xs',
                'text-neutral-400 hover:text-neutral-200',
                'hover:bg-neutral-800',
                animations.press,
                transitions.colors
              )}
              title="Copy output"
            >
              {copied ? (
                <>
                  <Check className="h-3.5 w-3.5 text-green-400" weight="bold" />
                  <span className="text-green-400">Copied</span>
                </>
              ) : (
                <>
                  <Copy className="h-3.5 w-3.5" weight="regular" />
                  <span>Copy</span>
                </>
              )}
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      {!isCollapsed && (
        <div
          className="overflow-auto"
          style={{ maxHeight }}
        >
          <pre className="p-3 text-sm font-mono leading-relaxed text-neutral-200">
            <code>{formattedResult}</code>
          </pre>
        </div>
      )}
    </FadeSlide>
  );
});

/**
 * ToolCallResultCompact - Inline result preview
 */
export interface ToolCallResultCompactProps {
  result: unknown;
  maxLength?: number;
  className?: string;
}

export const ToolCallResultCompact = memo(function ToolCallResultCompact({
  result,
  maxLength = 100,
  className,
}: ToolCallResultCompactProps) {
  const preview = (() => {
    if (result === null) return 'null';
    if (result === undefined) return 'undefined';
    if (typeof result === 'string') {
      return result.length > maxLength
        ? result.slice(0, maxLength) + '...'
        : result;
    }
    const str = JSON.stringify(result);
    return str.length > maxLength
      ? str.slice(0, maxLength) + '...'
      : str;
  })();

  return (
    <code
      className={cn(
        'text-xs font-mono px-2 py-1 rounded bg-muted text-muted-foreground',
        className
      )}
    >
      {preview}
    </code>
  );
});

export type { ToolCallResultProps as ToolCallResultComponentProps };
