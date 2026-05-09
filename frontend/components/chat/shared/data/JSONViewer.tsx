'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  BracketsCurly,
  CaretDown,
  CaretRight,
  Copy,
  Check,
  ArrowsOutSimple,
  X,
} from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { SpringCard } from '../primitives/SpringCard';
import { animations, transitions } from '@/lib/animation-utils';

export interface JSONViewerProps {
  /** JSON data to display */
  data: unknown;
  /** Title */
  title?: string;
  /** Default collapsed depth (0 = all expanded) */
  defaultCollapsedDepth?: number;
  /** Maximum depth to render */
  maxDepth?: number;
  /** Enable search */
  searchable?: boolean;
  /** Enable copy */
  copyable?: boolean;
  /** Enable fullscreen */
  expandable?: boolean;
  /** Maximum height before scrolling */
  maxHeight?: number;
  /** Root key name */
  rootName?: string;
  /** Compact mode */
  compact?: boolean;
  /** Additional CSS classes */
  className?: string;
}

interface JSONNodeProps {
  data: unknown;
  name?: string;
  depth: number;
  defaultCollapsedDepth: number;
  maxDepth: number;
  isLast?: boolean;
}

const JSONNode = memo(function JSONNode({
  data,
  name,
  depth,
  defaultCollapsedDepth,
  maxDepth,
  isLast = true,
}: JSONNodeProps) {
  const [isCollapsed, setIsCollapsed] = useState(depth >= defaultCollapsedDepth);

  if (depth > maxDepth) {
    return <span className="text-muted-foreground">...</span>;
  }

  // Primitive values
  if (data === null) {
    return (
      <span className="text-purple-400 italic">null</span>
    );
  }

  if (data === undefined) {
    return (
      <span className="text-neutral-500 italic">undefined</span>
    );
  }

  if (typeof data === 'boolean') {
    return (
      <span className={data ? 'text-green-400' : 'text-red-400'}>
        {String(data)}
      </span>
    );
  }

  if (typeof data === 'number') {
    return <span className="text-orange-400">{data}</span>;
  }

  if (typeof data === 'string') {
    return (
      <span className="text-green-400">&quot;{data}&quot;</span>
    );
  }

  // Arrays
  if (Array.isArray(data)) {
    if (data.length === 0) {
      return <span className="text-neutral-400">[]</span>;
    }

    return (
      <span>
        <button
          onClick={() => setIsCollapsed((prev) => !prev)}
          className={cn(
            'inline-flex items-center gap-0.5 text-neutral-400 hover:text-neutral-200',
            transitions.colors
          )}
        >
          {isCollapsed ? (
            <CaretRight className="h-3 w-3" />
          ) : (
            <CaretDown className="h-3 w-3" />
          )}
          <span>[</span>
        </button>
        {isCollapsed ? (
          <span className="text-neutral-500">
            {' '}{data.length} items{' '}
          </span>
        ) : (
          <div className="ml-4">
            {data.map((item, index) => (
              <div key={index}>
                <span className="text-neutral-500">{index}: </span>
                <JSONNode
                  data={item}
                  depth={depth + 1}
                  defaultCollapsedDepth={defaultCollapsedDepth}
                  maxDepth={maxDepth}
                  isLast={index === data.length - 1}
                />
                {index < data.length - 1 && <span className="text-neutral-400">,</span>}
              </div>
            ))}
          </div>
        )}
        <span className="text-neutral-400">]</span>
        {!isLast && <span className="text-neutral-400">,</span>}
      </span>
    );
  }

  // Objects
  if (typeof data === 'object') {
    const entries = Object.entries(data as Record<string, unknown>);

    if (entries.length === 0) {
      return <span className="text-neutral-400">{'{}'}</span>;
    }

    return (
      <span>
        <button
          onClick={() => setIsCollapsed((prev) => !prev)}
          className={cn(
            'inline-flex items-center gap-0.5 text-neutral-400 hover:text-neutral-200',
            transitions.colors
          )}
        >
          {isCollapsed ? (
            <CaretRight className="h-3 w-3" />
          ) : (
            <CaretDown className="h-3 w-3" />
          )}
          <span>{'{'}</span>
        </button>
        {isCollapsed ? (
          <span className="text-neutral-500">
            {' '}{entries.length} keys{' '}
          </span>
        ) : (
          <div className="ml-4">
            {entries.map(([key, value], index) => (
              <div key={key}>
                <span className="text-blue-300">&quot;{key}&quot;</span>
                <span className="text-neutral-400">: </span>
                <JSONNode
                  data={value}
                  depth={depth + 1}
                  defaultCollapsedDepth={defaultCollapsedDepth}
                  maxDepth={maxDepth}
                  isLast={index === entries.length - 1}
                />
                {index < entries.length - 1 && <span className="text-neutral-400">,</span>}
              </div>
            ))}
          </div>
        )}
        <span className="text-neutral-400">{'}'}</span>
        {!isLast && <span className="text-neutral-400">,</span>}
      </span>
    );
  }

  return <span className="text-neutral-200">{String(data)}</span>;
});

/**
 * JSONViewer - Interactive JSON tree viewer
 *
 * Features:
 * - Collapsible nodes
 * - Syntax highlighting
 * - Copy functionality
 * - Fullscreen mode
 */
export const JSONViewer = memo(function JSONViewer({
  data,
  title,
  defaultCollapsedDepth = 2,
  maxDepth = 10,
  searchable = false,
  copyable = true,
  expandable = true,
  maxHeight = 400,
  rootName,
  compact = false,
  className,
}: JSONViewerProps) {
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [data]);

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
            <div className="flex items-center gap-2">
              <BracketsCurly className="h-4 w-4 text-primary" weight="bold" />
              <span className="text-xs text-neutral-400">
                {title || 'JSON'}
              </span>
            </div>

            <div className="flex items-center gap-1">
              {searchable && (
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search..."
                  className={cn(
                    'px-2 py-1 text-xs rounded border border-neutral-700 bg-neutral-800',
                    'text-neutral-200 placeholder:text-neutral-500',
                    'focus:outline-none focus:border-primary',
                    transitions.colors
                  )}
                />
              )}

              {copyable && (
                <button
                  onClick={handleCopy}
                  className={cn(
                    'p-1.5 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800',
                    animations.press,
                    transitions.colors
                  )}
                  title="Copy JSON"
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
          <div
            className="overflow-auto p-3 font-mono text-sm leading-relaxed"
            style={{ maxHeight }}
          >
            {rootName && (
              <span className="text-blue-300">&quot;{rootName}&quot;</span>
            )}
            {rootName && <span className="text-neutral-400">: </span>}
            <JSONNode
              data={data}
              depth={0}
              defaultCollapsedDepth={defaultCollapsedDepth}
              maxDepth={maxDepth}
            />
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
              <div className="flex items-center gap-2">
                <BracketsCurly className="h-5 w-5 text-primary" weight="bold" />
                <span className="text-sm text-neutral-200">
                  {title || 'JSON Viewer'}
                </span>
              </div>

              <div className="flex items-center gap-2">
                {copyable && (
                  <button
                    onClick={handleCopy}
                    className={cn(
                      'flex items-center gap-1 px-2 py-1 rounded text-sm',
                      'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800',
                      animations.press,
                      transitions.colors
                    )}
                  >
                    {copied ? (
                      <>
                        <Check className="h-4 w-4 text-green-400" weight="bold" />
                        <span className="text-green-400">Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="h-4 w-4" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>
                )}
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
            </div>

            {/* Content */}
            <div className="overflow-auto h-[calc(100%-52px)] p-4 font-mono text-sm leading-relaxed">
              <JSONNode
                data={data}
                depth={0}
                defaultCollapsedDepth={10}
                maxDepth={20}
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
});

export type { JSONViewerProps as JSONViewerComponentProps };
