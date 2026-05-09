'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Buildings, Eye, EyeSlash, Brain, WarningCircle } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { ActionButton } from './ActionButton';

export interface OrgShareCardProps {
  /** Unique identifier for the memory */
  memoryId: string;
  /** Summary of the memory content */
  summary: string;
  /** Agent that created the memory */
  agentName: string;
  /** Current visibility */
  currentVisibility: 'private' | 'shared';
  /** Target visibility being proposed */
  targetVisibility: 'private' | 'shared';
  /** Current status of the share request */
  status?: 'pending' | 'shared' | 'kept_private' | 'blocked';
  /** Reason if blocked (e.g., PII detected) */
  blockedReason?: string;
  /** Callback when user confirms sharing */
  onShare?: () => void;
  /** Callback when user keeps private */
  onKeepPrivate?: () => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * OrgShareCard - Inline card for sharing memories with the organization
 *
 * Displayed in chat when an agent suggests sharing a decision/insight
 * with the organization's shared memory pool. Follows the SpringCard
 * pattern from ApprovalCard.
 *
 * Features:
 * - Preview of memory content being shared
 * - PII block state with explanation
 * - Share / Keep Private actions
 * - Status feedback after action
 */
export const OrgShareCard = memo(function OrgShareCard({
  memoryId,
  summary,
  agentName,
  currentVisibility,
  targetVisibility,
  status = 'pending',
  blockedReason,
  onShare,
  onKeepPrivate,
  className,
}: OrgShareCardProps) {
  const [isProcessing, setIsProcessing] = useState(false);
  const isPending = status === 'pending';
  const isBlocked = status === 'blocked';
  const isSharingAction = targetVisibility === 'shared';

  const handleShare = useCallback(async () => {
    if (!onShare) return;
    setIsProcessing(true);
    try {
      await onShare();
    } finally {
      setIsProcessing(false);
    }
  }, [onShare]);

  const handleKeepPrivate = useCallback(async () => {
    if (!onKeepPrivate) return;
    setIsProcessing(true);
    try {
      await onKeepPrivate();
    } finally {
      setIsProcessing(false);
    }
  }, [onKeepPrivate]);

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        className={cn(
          'overflow-hidden',
          isBlocked && 'border-amber-500/30 bg-amber-500/5',
          status === 'shared' && 'border-blue-500/30 bg-blue-500/5',
          status === 'kept_private' && 'border-border',
          className,
        )}
      >
        {/* Header */}
        <div className="flex items-start gap-3">
          <div
            className={cn(
              'flex items-center justify-center w-10 h-10 rounded-lg shrink-0',
              isBlocked
                ? 'bg-amber-500/10'
                : isSharingAction
                  ? 'bg-blue-500/10'
                  : 'bg-muted',
            )}
          >
            {isBlocked ? (
              <WarningCircle className="h-5 w-5 text-amber-500" weight="fill" />
            ) : isSharingAction ? (
              <Buildings className="h-5 w-5 text-blue-500" weight="fill" />
            ) : (
              <EyeSlash className="h-5 w-5 text-muted-foreground" weight="fill" />
            )}
          </div>

          <div className="flex-1 min-w-0">
            <h4 className="font-semibold text-sm">
              {isBlocked
                ? 'Cannot Share Memory'
                : isSharingAction
                  ? 'Share with Organization?'
                  : 'Make Memory Private?'}
            </h4>
            <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
              <Brain className="h-3 w-3" />
              <span>{agentName}</span>
            </div>
          </div>
        </div>

        {/* Memory Preview */}
        <div className="mt-3 p-3 bg-muted/50 rounded-lg">
          <p className="text-sm text-foreground line-clamp-3">{summary}</p>
        </div>

        {/* Blocked Reason */}
        {isBlocked && blockedReason && (
          <div className="mt-3 flex items-start gap-2 text-sm text-amber-700">
            <WarningCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{blockedReason}</span>
          </div>
        )}

        {/* Visibility Change Indicator */}
        {isPending && !isBlocked && (
          <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              {currentVisibility === 'private' ? (
                <EyeSlash className="h-3.5 w-3.5" />
              ) : (
                <Buildings className="h-3.5 w-3.5" />
              )}
              {currentVisibility}
            </span>
            <span>&rarr;</span>
            <span className="flex items-center gap-1 font-medium text-foreground">
              {targetVisibility === 'shared' ? (
                <Buildings className="h-3.5 w-3.5 text-blue-500" />
              ) : (
                <EyeSlash className="h-3.5 w-3.5" />
              )}
              {targetVisibility}
            </span>
          </div>
        )}

        {/* Actions */}
        {isPending && !isBlocked && (onShare || onKeepPrivate) && (
          <div className="flex items-center gap-2 mt-4 pt-4 border-t border-border">
            {onKeepPrivate && (
              <ActionButton
                variant="outline"
                size="sm"
                onClick={handleKeepPrivate}
                disabled={isProcessing}
                icon={<EyeSlash className="h-3.5 w-3.5" />}
              >
                Keep Private
              </ActionButton>
            )}
            {onShare && (
              <ActionButton
                variant="primary"
                size="sm"
                onClick={handleShare}
                loading={isProcessing}
                icon={!isProcessing ? <Buildings className="h-3.5 w-3.5" weight="bold" /> : undefined}
              >
                Share with Org
              </ActionButton>
            )}
          </div>
        )}

        {/* Status Feedback */}
        {status === 'shared' && (
          <div className="mt-4 pt-4 border-t border-blue-500/20 flex items-center gap-2 text-sm text-blue-500">
            <Buildings className="h-4 w-4" weight="fill" />
            <span>Shared with organization</span>
          </div>
        )}

        {status === 'kept_private' && (
          <div className="mt-4 pt-4 border-t border-border flex items-center gap-2 text-sm text-muted-foreground">
            <EyeSlash className="h-4 w-4" />
            <span>Kept private</span>
          </div>
        )}

        {isBlocked && (
          <div className="mt-4 pt-4 border-t border-amber-500/20 flex items-center gap-2 text-sm text-amber-600">
            <WarningCircle className="h-4 w-4" />
            <span>Sharing blocked — remove sensitive data first</span>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { OrgShareCardProps as OrgShareCardComponentProps };
