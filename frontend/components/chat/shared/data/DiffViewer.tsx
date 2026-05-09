'use client';

import { memo, useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { GitDiff, Copy, Check, ArrowsOutSimple, X } from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { SpringCard } from '../primitives/SpringCard';
import { animations, transitions } from '@/lib/animation-utils';

export interface DiffLine {
  type: 'add' | 'remove' | 'context' | 'header';
  content: string;
  lineNumber?: {
    old?: number;
    new?: number;
  };
}

export interface DiffViewerProps {
  /** Diff content as string (unified diff format) */
  diff?: string;
  /** Pre-parsed diff lines */
  lines?: DiffLine[];
  /** Title */
  title?: string;
  /** Old file name */
  oldFileName?: string;
  /** New file name */
  newFileName?: string;
  /** Show line numbers */
  showLineNumbers?: boolean;
  /** Enable copy */
  copyable?: boolean;
  /** Enable fullscreen */
  expandable?: boolean;
  /** Maximum height before scrolling */
  maxHeight?: number;
  /** Compact mode */
  compact?: boolean;
  /** Additional CSS classes */
  className?: string;
}

// Parse unified diff format
const parseDiff = (diff: string): DiffLine[] => {
  const lines = diff.split('\n');
  const result: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;

  for (const line of lines) {
    if (line.startsWith('@@')) {
      // Hunk header
      const match = line.match(/@@ -(\d+),?\d* \+(\d+),?\d* @@/);
      if (match) {
        oldLine = parseInt(match[1], 10);
        newLine = parseInt(match[2], 10);
      }
      result.push({ type: 'header', content: line });
    } else if (line.startsWith('+') && !line.startsWith('+++')) {
      result.push({
        type: 'add',
        content: line.slice(1),
        lineNumber: { new: newLine },
      });
      newLine++;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      result.push({
        type: 'remove',
        content: line.slice(1),
        lineNumber: { old: oldLine },
      });
      oldLine++;
    } else if (line.startsWith(' ')) {
      result.push({
        type: 'context',
        content: line.slice(1),
        lineNumber: { old: oldLine, new: newLine },
      });
      oldLine++;
      newLine++;
    } else if (!line.startsWith('---') && !line.startsWith('+++') && !line.startsWith('diff')) {
      // Other lines (context without leading space)
      result.push({
        type: 'context',
        content: line,
        lineNumber: { old: oldLine, new: newLine },
      });
      oldLine++;
      newLine++;
    }
  }

  return result;
};

/**
 * DiffViewer - Display code diffs
 *
 * Features:
 * - Unified diff format support
 * - Line numbers
 * - Add/remove highlighting
 * - Fullscreen mode
 */
export const DiffViewer = memo(function DiffViewer({
  diff,
  lines: propLines,
  title,
  oldFileName,
  newFileName,
  showLineNumbers = true,
  copyable = true,
  expandable = true,
  maxHeight = 400,
  compact = false,
  className,
}: DiffViewerProps) {
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  const lines = useMemo(() => {
    if (propLines) return propLines;
    if (diff) return parseDiff(diff);
    return [];
  }, [propLines, diff]);

  const stats = useMemo(() => {
    const additions = lines.filter((l) => l.type === 'add').length;
    const deletions = lines.filter((l) => l.type === 'remove').length;
    return { additions, deletions };
  }, [lines]);

  const handleCopy = async () => {
    const content = diff || lines.map((l) => {
      if (l.type === 'add') return `+${l.content}`;
      if (l.type === 'remove') return `-${l.content}`;
      if (l.type === 'header') return l.content;
      return ` ${l.content}`;
    }).join('\n');

    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const renderLines = (linesData: DiffLine[]) => (
    <div className="font-mono text-xs leading-relaxed">
      {linesData.map((line, index) => (
        <div
          key={index}
          className={cn(
            'flex',
            line.type === 'add' && 'bg-green-500/10',
            line.type === 'remove' && 'bg-red-500/10',
            line.type === 'header' && 'bg-blue-500/10'
          )}
        >
          {/* Line numbers */}
          {showLineNumbers && (
            <div className="flex shrink-0 text-neutral-500 select-none">
              <span className="w-10 px-2 text-right border-r border-neutral-800">
                {line.lineNumber?.old ?? ''}
              </span>
              <span className="w-10 px-2 text-right border-r border-neutral-800">
                {line.lineNumber?.new ?? ''}
              </span>
            </div>
          )}

          {/* Sign */}
          <span
            className={cn(
              'w-6 px-1 text-center shrink-0',
              line.type === 'add' && 'text-green-400',
              line.type === 'remove' && 'text-red-400',
              line.type === 'header' && 'text-blue-400'
            )}
          >
            {line.type === 'add' ? '+' : line.type === 'remove' ? '-' : line.type === 'header' ? '@@' : ' '}
          </span>

          {/* Content */}
          <code
            className={cn(
              'flex-1 px-2 whitespace-pre',
              line.type === 'add' && 'text-green-300',
              line.type === 'remove' && 'text-red-300',
              line.type === 'header' && 'text-blue-300',
              line.type === 'context' && 'text-neutral-300'
            )}
          >
            {line.content}
          </code>
        </div>
      ))}
    </div>
  );

  return (
    <>
      <FadeSlide>
        <SpringCard
          variant="outline"
          size={compact ? 'sm' : 'md'}
          className={cn('bg-neutral-950 overflow-hidden', className)}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-2 bg-neutral-900 border-b border-neutral-800">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <GitDiff className="h-4 w-4 text-primary" weight="fill" />
                <span className="text-xs text-neutral-400">
                  {title || 'Diff'}
                </span>
              </div>

              {/* File names */}
              {(oldFileName || newFileName) && (
                <div className="flex items-center gap-2 text-xs">
                  {oldFileName && (
                    <span className="text-red-400">{oldFileName}</span>
                  )}
                  {oldFileName && newFileName && (
                    <span className="text-neutral-600">→</span>
                  )}
                  {newFileName && (
                    <span className="text-green-400">{newFileName}</span>
                  )}
                </div>
              )}

              {/* Stats */}
              <div className="flex items-center gap-2 text-xs">
                <span className="text-green-400">+{stats.additions}</span>
                <span className="text-red-400">-{stats.deletions}</span>
              </div>
            </div>

            <div className="flex items-center gap-1">
              {copyable && (
                <button
                  onClick={handleCopy}
                  className={cn(
                    'p-1.5 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800',
                    animations.press,
                    transitions.colors
                  )}
                  title="Copy diff"
                >
                  {copied ? (
                    <Check className="h-3.5 w-3.5 text-green-400" weight="bold" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                </button>
              )}

              {expandable && (
                <button
                  onClick={() => setIsExpanded(true)}
                  className={cn(
                    'p-1.5 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800',
                    animations.press,
                    transitions.colors
                  )}
                  title="Expand"
                >
                  <ArrowsOutSimple className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* Content */}
          <div className="overflow-auto" style={{ maxHeight }}>
            {renderLines(lines)}
          </div>
        </SpringCard>
      </FadeSlide>

      {/* Fullscreen modal */}
      {isExpanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm animate-fade-in"
          onClick={() => setIsExpanded(false)}
        >
          <div
            className="relative w-[90vw] h-[90vh] bg-neutral-950 rounded-lg overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 bg-neutral-900 border-b border-neutral-800">
              <div className="flex items-center gap-3">
                <GitDiff className="h-5 w-5 text-primary" weight="fill" />
                <span className="text-sm text-neutral-200">{title || 'Diff Viewer'}</span>
                <span className="text-green-400">+{stats.additions}</span>
                <span className="text-red-400">-{stats.deletions}</span>
              </div>

              <button
                onClick={() => setIsExpanded(false)}
                className={cn(
                  'p-2 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800',
                  animations.press,
                  transitions.colors
                )}
              >
                <X className="h-5 w-5" weight="bold" />
              </button>
            </div>

            {/* Content */}
            <div className="overflow-auto h-[calc(100%-52px)]">
              {renderLines(lines)}
            </div>
          </div>
        </div>
      )}
    </>
  );
});

export type { DiffViewerProps as DiffViewerComponentProps };
