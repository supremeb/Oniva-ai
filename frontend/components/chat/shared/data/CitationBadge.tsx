'use client';

import { memo, useState } from 'react';
import { cn } from '@/lib/utils';
import { FileText, ArrowSquareOut, X } from '@phosphor-icons/react';
import { animations, transitions } from '@/lib/animation-utils';

export interface CitationBadgeProps {
  /** Citation number */
  number: number;
  /** Citation title */
  title?: string;
  /** Source URL */
  url?: string;
  /** Show tooltip on hover */
  showTooltip?: boolean;
  /** Tooltip content */
  tooltipContent?: React.ReactNode;
  /** Callback when clicked */
  onClick?: () => void;
  /** Variant style */
  variant?: 'default' | 'inline' | 'superscript';
  /** Additional CSS classes */
  className?: string;
}

/**
 * CitationBadge - Inline citation reference
 *
 * Features:
 * - Multiple display variants
 * - Hover tooltip
 * - Link to source
 */
export const CitationBadge = memo(function CitationBadge({
  number,
  title,
  url,
  showTooltip = true,
  tooltipContent,
  onClick,
  variant = 'default',
  className,
}: CitationBadgeProps) {
  const [showPopover, setShowPopover] = useState(false);

  const handleClick = () => {
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
    onClick?.();
  };

  if (variant === 'superscript') {
    return (
      <sup
        className={cn(
          'relative inline-flex items-center justify-center',
          'w-4 h-4 ml-0.5 -top-1',
          'text-[10px] font-medium text-primary',
          'bg-primary/10 rounded-full',
          (url || onClick) && 'cursor-pointer hover:bg-primary/20',
          transitions.colors,
          className
        )}
        onClick={handleClick}
        onMouseEnter={() => showTooltip && setShowPopover(true)}
        onMouseLeave={() => setShowPopover(false)}
      >
        {number}

        {/* Tooltip */}
        {showPopover && (title || tooltipContent) && (
          <div
            className={cn(
              'absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50',
              'px-2 py-1 rounded-md bg-popover border border-border shadow-lg',
              'text-xs text-popover-foreground whitespace-nowrap',
              'animate-fade-in'
            )}
          >
            {tooltipContent || title}
          </div>
        )}
      </sup>
    );
  }

  if (variant === 'inline') {
    return (
      <span
        className={cn(
          'relative inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs',
          'bg-primary/10 text-primary font-medium',
          (url || onClick) && 'cursor-pointer hover:bg-primary/20',
          transitions.colors,
          className
        )}
        onClick={handleClick}
        onMouseEnter={() => showTooltip && setShowPopover(true)}
        onMouseLeave={() => setShowPopover(false)}
      >
        [{number}]

        {/* Tooltip */}
        {showPopover && (title || tooltipContent) && (
          <div
            className={cn(
              'absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50',
              'px-2 py-1 rounded-md bg-popover border border-border shadow-lg',
              'text-xs text-popover-foreground whitespace-nowrap max-w-xs truncate',
              'animate-fade-in'
            )}
          >
            {tooltipContent || title}
          </div>
        )}
      </span>
    );
  }

  // Default variant with hover popover
  return (
    <span className="relative inline-block">
      <button
        className={cn(
          'inline-flex items-center justify-center',
          'w-5 h-5 rounded-md',
          'text-xs font-medium text-primary',
          'bg-primary/10 hover:bg-primary/20',
          animations.press,
          transitions.colors,
          className
        )}
        onClick={handleClick}
        onMouseEnter={() => showTooltip && setShowPopover(true)}
        onMouseLeave={() => setShowPopover(false)}
      >
        {number}
      </button>

      {/* Popover */}
      {showPopover && (title || tooltipContent || url) && (
        <div
          className={cn(
            'absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50',
            'w-64 p-3 rounded-lg bg-popover border border-border shadow-lg',
            'animate-fade-in'
          )}
          onMouseEnter={() => setShowPopover(true)}
          onMouseLeave={() => setShowPopover(false)}
        >
          {tooltipContent ? (
            tooltipContent
          ) : (
            <>
              <div className="flex items-start gap-2">
                <FileText className="h-4 w-4 text-primary shrink-0 mt-0.5" weight="fill" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm line-clamp-2">{title}</div>
                  {url && (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={cn(
                        'flex items-center gap-1 mt-1 text-xs text-primary hover:underline',
                        transitions.colors
                      )}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <span className="truncate">{url}</span>
                      <ArrowSquareOut className="h-3 w-3 shrink-0" />
                    </a>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </span>
  );
});

export type { CitationBadgeProps as CitationBadgeComponentProps };
