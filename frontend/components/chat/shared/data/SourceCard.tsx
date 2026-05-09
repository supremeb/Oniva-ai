'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import {
  FileText,
  Link as LinkIcon,
  Database,
  File,
  Globe,
  ArrowSquareOut,
} from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export type SourceType = 'document' | 'url' | 'database' | 'file' | 'api';

export interface SourceCardProps {
  /** Source type */
  type: SourceType;
  /** Source title */
  title: string;
  /** Source description or excerpt */
  description?: string;
  /** Source URL (for links) */
  url?: string;
  /** Source metadata */
  metadata?: {
    author?: string;
    date?: string;
    pages?: string;
    relevance?: number; // 0-100
  };
  /** Citation number */
  citationNumber?: number;
  /** Compact display */
  compact?: boolean;
  /** Callback when clicked */
  onClick?: () => void;
  /** Additional CSS classes */
  className?: string;
}

const sourceIcons = {
  document: FileText,
  url: Globe,
  database: Database,
  file: File,
  api: LinkIcon,
};

const sourceColors = {
  document: 'bg-blue-500/10 text-blue-500',
  url: 'bg-green-500/10 text-green-500',
  database: 'bg-purple-500/10 text-purple-500',
  file: 'bg-orange-500/10 text-orange-500',
  api: 'bg-pink-500/10 text-pink-500',
};

/**
 * SourceCard - Display source/citation information
 *
 * Features:
 * - Multiple source types
 * - Metadata display
 * - Relevance indicator
 * - Clickable for details
 */
export const SourceCard = memo(function SourceCard({
  type,
  title,
  description,
  url,
  metadata,
  citationNumber,
  compact = false,
  onClick,
  className,
}: SourceCardProps) {
  const SourceIcon = sourceIcons[type] || File;
  const iconColor = sourceColors[type];

  const handleClick = () => {
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
    onClick?.();
  };

  if (compact) {
    return (
      <button
        onClick={handleClick}
        className={cn(
          'inline-flex items-center gap-2 px-2 py-1 rounded-md border border-border',
          'text-sm hover:bg-muted/50',
          (url || onClick) && 'cursor-pointer',
          animations.press,
          transitions.colors,
          className
        )}
      >
        {citationNumber && (
          <span className="flex items-center justify-center w-5 h-5 rounded bg-primary/10 text-xs font-medium text-primary">
            {citationNumber}
          </span>
        )}
        <SourceIcon className={cn('h-3.5 w-3.5', iconColor.split(' ')[1])} weight="fill" />
        <span className="truncate max-w-[200px]">{title}</span>
        {url && <ArrowSquareOut className="h-3 w-3 text-muted-foreground shrink-0" />}
      </button>
    );
  }

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        size="sm"
        className={cn(
          (url || onClick) && 'cursor-pointer hover:border-primary/30',
          className
        )}
        onClick={handleClick}
      >
        <div className="flex items-start gap-3">
          {/* Icon */}
          <div
            className={cn(
              'flex items-center justify-center w-10 h-10 rounded-lg shrink-0',
              iconColor
            )}
          >
            <SourceIcon className="h-5 w-5" weight="fill" />
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              {citationNumber && (
                <span className="flex items-center justify-center w-5 h-5 rounded bg-primary/10 text-xs font-medium text-primary shrink-0">
                  {citationNumber}
                </span>
              )}
              <h4 className="font-medium text-sm truncate">{title}</h4>
              {url && (
                <ArrowSquareOut className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              )}
            </div>

            {description && (
              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                {description}
              </p>
            )}

            {/* Metadata */}
            {metadata && (
              <div className="flex items-center gap-3 mt-2 text-xs text-muted-foreground">
                {metadata.author && <span>{metadata.author}</span>}
                {metadata.date && <span>{metadata.date}</span>}
                {metadata.pages && <span>Pages: {metadata.pages}</span>}
                {metadata.relevance !== undefined && (
                  <div className="flex items-center gap-1">
                    <div className="w-12 h-1 rounded-full bg-muted overflow-hidden">
                      <div
                        className={cn(
                          'h-full rounded-full',
                          metadata.relevance >= 70
                            ? 'bg-green-500'
                            : metadata.relevance >= 40
                            ? 'bg-yellow-500'
                            : 'bg-red-500'
                        )}
                        style={{ width: `${metadata.relevance}%` }}
                      />
                    </div>
                    <span>{metadata.relevance}%</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </SpringCard>
    </FadeSlide>
  );
});

export type { SourceCardProps as SourceCardComponentProps };
