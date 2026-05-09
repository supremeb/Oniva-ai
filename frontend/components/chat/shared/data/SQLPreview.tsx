'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Database, Play, Copy, Check, Clock, Warning } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface SQLPreviewProps {
  /** SQL query string */
  query: string;
  /** Database name or connection */
  database?: string;
  /** Execution status */
  status?: 'idle' | 'running' | 'success' | 'error';
  /** Execution time in milliseconds */
  executionTime?: number;
  /** Rows affected/returned */
  rowCount?: number;
  /** Error message */
  error?: string;
  /** Enable syntax highlighting */
  syntaxHighlight?: boolean;
  /** Enable line numbers */
  showLineNumbers?: boolean;
  /** Enable copy button */
  copyable?: boolean;
  /** Enable execute button */
  executable?: boolean;
  /** Callback when execute clicked */
  onExecute?: () => void;
  /** Compact mode */
  compact?: boolean;
  /** Additional CSS classes */
  className?: string;
}

// Simple SQL syntax highlighting
const highlightSQL = (sql: string): React.ReactNode[] => {
  const keywords = [
    'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'IN', 'BETWEEN', 'LIKE',
    'IS', 'NULL', 'AS', 'JOIN', 'INNER', 'LEFT', 'RIGHT', 'OUTER', 'FULL',
    'ON', 'GROUP', 'BY', 'HAVING', 'ORDER', 'ASC', 'DESC', 'LIMIT', 'OFFSET',
    'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE', 'CREATE', 'ALTER',
    'DROP', 'TABLE', 'INDEX', 'VIEW', 'DATABASE', 'SCHEMA', 'PRIMARY', 'KEY',
    'FOREIGN', 'REFERENCES', 'UNIQUE', 'CHECK', 'DEFAULT', 'AUTO_INCREMENT',
    'UNION', 'ALL', 'DISTINCT', 'TOP', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'COALESCE', 'CAST', 'CONVERT',
  ];

  const functions = [
    'NOW', 'DATE', 'TIME', 'YEAR', 'MONTH', 'DAY', 'HOUR', 'MINUTE', 'SECOND',
    'CONCAT', 'SUBSTRING', 'LENGTH', 'TRIM', 'UPPER', 'LOWER', 'REPLACE',
    'ROUND', 'FLOOR', 'CEIL', 'ABS', 'MOD', 'POWER', 'SQRT',
  ];

  const parts: React.ReactNode[] = [];
  const regex = /('[^']*'|"[^"]*"|--.*$|\/\*[\s\S]*?\*\/|\b\d+\.?\d*\b|\b\w+\b|[^\w\s]+)/gm;

  let match;
  let lastIndex = 0;
  let key = 0;

  while ((match = regex.exec(sql)) !== null) {
    // Add any text before this match
    if (match.index > lastIndex) {
      parts.push(sql.slice(lastIndex, match.index));
    }

    const token = match[0];
    const upperToken = token.toUpperCase();

    if (token.startsWith("'") || token.startsWith('"')) {
      // String
      parts.push(
        <span key={key++} className="text-green-400">
          {token}
        </span>
      );
    } else if (token.startsWith('--') || token.startsWith('/*')) {
      // Comment
      parts.push(
        <span key={key++} className="text-neutral-500 italic">
          {token}
        </span>
      );
    } else if (/^\d+\.?\d*$/.test(token)) {
      // Number
      parts.push(
        <span key={key++} className="text-orange-400">
          {token}
        </span>
      );
    } else if (keywords.includes(upperToken)) {
      // Keyword
      parts.push(
        <span key={key++} className="text-blue-400 font-medium">
          {token}
        </span>
      );
    } else if (functions.includes(upperToken)) {
      // Function
      parts.push(
        <span key={key++} className="text-purple-400">
          {token}
        </span>
      );
    } else {
      parts.push(token);
    }

    lastIndex = regex.lastIndex;
  }

  // Add any remaining text
  if (lastIndex < sql.length) {
    parts.push(sql.slice(lastIndex));
  }

  return parts;
};

/**
 * SQLPreview - Display SQL queries with syntax highlighting
 *
 * Features:
 * - Syntax highlighting
 * - Line numbers
 * - Execute button
 * - Copy functionality
 * - Execution status
 */
export const SQLPreview = memo(function SQLPreview({
  query,
  database,
  status = 'idle',
  executionTime,
  rowCount,
  error,
  syntaxHighlight = true,
  showLineNumbers = true,
  copyable = true,
  executable = false,
  onExecute,
  compact = false,
  className,
}: SQLPreviewProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(query);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [query]);

  const lines = query.split('\n');
  const lineNumberWidth = Math.max(2, String(lines.length).length);

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        size={compact ? 'sm' : 'md'}
        className={cn('overflow-hidden bg-neutral-950', className)}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 bg-neutral-900 border-b border-neutral-800">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" weight="fill" />
            <span className="text-xs text-neutral-400">SQL Query</span>
            {database && (
              <>
                <span className="text-neutral-600">•</span>
                <span className="text-xs text-neutral-500">{database}</span>
              </>
            )}
          </div>

          {/* Status and actions */}
          <div className="flex items-center gap-2">
            {/* Execution status */}
            {status === 'running' && (
              <span className="flex items-center gap-1 text-xs text-primary animate-pulse">
                <Clock className="h-3 w-3" />
                Running...
              </span>
            )}
            {status === 'success' && executionTime !== undefined && (
              <span className="flex items-center gap-1 text-xs text-green-400">
                <Check className="h-3 w-3" weight="bold" />
                {executionTime}ms
                {rowCount !== undefined && ` • ${rowCount} rows`}
              </span>
            )}
            {status === 'error' && (
              <span className="flex items-center gap-1 text-xs text-red-400">
                <Warning className="h-3 w-3" weight="fill" />
                Error
              </span>
            )}

            {/* Copy button */}
            {copyable && (
              <button
                onClick={handleCopy}
                className={cn(
                  'p-1.5 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800',
                  animations.press,
                  transitions.colors
                )}
                title="Copy query"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5 text-green-400" weight="bold" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </button>
            )}

            {/* Execute button */}
            {executable && onExecute && (
              <button
                onClick={onExecute}
                disabled={status === 'running'}
                className={cn(
                  'flex items-center gap-1 px-2 py-1 rounded text-xs',
                  'bg-primary/20 text-primary hover:bg-primary/30',
                  'disabled:opacity-50 disabled:cursor-not-allowed',
                  animations.press,
                  transitions.colors
                )}
              >
                <Play className="h-3 w-3" weight="fill" />
                Run
              </button>
            )}
          </div>
        </div>

        {/* Query */}
        <div className="overflow-auto max-h-80">
          <pre className="p-3 text-sm font-mono leading-relaxed">
            {lines.map((line, index) => (
              <div key={index} className="flex">
                {showLineNumbers && (
                  <span
                    className="text-neutral-600 select-none pr-4 text-right"
                    style={{ minWidth: `${lineNumberWidth + 2}ch` }}
                  >
                    {index + 1}
                  </span>
                )}
                <code className="text-neutral-200">
                  {syntaxHighlight ? highlightSQL(line) : line}
                </code>
              </div>
            ))}
          </pre>
        </div>

        {/* Error message */}
        {status === 'error' && error && (
          <div className="px-3 py-2 bg-red-500/10 border-t border-red-500/20">
            <p className="text-xs text-red-400 font-mono">{error}</p>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { SQLPreviewProps as SQLPreviewComponentProps };
