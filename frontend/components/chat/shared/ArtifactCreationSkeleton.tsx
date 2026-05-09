'use client';

/**
 * ArtifactCreationSkeleton — Shimmer placeholder shown while a
 * file-producing tool is running. Cross-fades to the real FilePreview
 * when the tool completes.
 */

import { memo } from 'react';
import { cn } from '@/lib/utils';
import { FileText, CircleNotch } from '@phosphor-icons/react';

export interface ArtifactCreationSkeletonProps {
  /** Expected filename (from tool args if available) */
  filename?: string;
  /** Tool name producing the file */
  toolName?: string;
  /** Additional CSS classes */
  className?: string;
}

export const ArtifactCreationSkeleton = memo(function ArtifactCreationSkeleton({
  filename,
  toolName,
  className,
}: ArtifactCreationSkeletonProps) {
  return (
    <div
      className={cn(
        'rounded-xl border border-border/50 p-4 space-y-3',
        'animate-fade-slide-up',
        className,
      )}
    >
      {/* Header: icon + filename + spinner */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
          <FileText size={20} className="text-primary animate-pulse" weight="fill" />
        </div>
        <div className="flex-1 min-w-0">
          {filename ? (
            <span className="text-sm font-medium truncate block">{filename}</span>
          ) : (
            <div className="h-4 w-32 rounded bg-muted animate-shimmer bg-gradient-to-r from-muted via-muted-foreground/10 to-muted bg-[length:200%_100%]" />
          )}
          {toolName && (
            <span className="text-xs text-muted-foreground mt-0.5 block">
              {toolName.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
            </span>
          )}
        </div>
        <CircleNotch size={16} className="animate-spin text-muted-foreground shrink-0" />
      </div>

      {/* Content skeleton lines */}
      <div className="space-y-2">
        <div
          className="h-3 rounded bg-muted animate-shimmer bg-gradient-to-r from-muted via-muted-foreground/10 to-muted bg-[length:200%_100%]"
          style={{ width: '100%' }}
        />
        <div
          className="h-3 rounded bg-muted animate-shimmer bg-gradient-to-r from-muted via-muted-foreground/10 to-muted bg-[length:200%_100%]"
          style={{ width: '85%', animationDelay: '0.15s' }}
        />
        <div
          className="h-3 rounded bg-muted animate-shimmer bg-gradient-to-r from-muted via-muted-foreground/10 to-muted bg-[length:200%_100%]"
          style={{ width: '70%', opacity: 0.5, animationDelay: '0.3s' }}
        />
      </div>
    </div>
  );
});
