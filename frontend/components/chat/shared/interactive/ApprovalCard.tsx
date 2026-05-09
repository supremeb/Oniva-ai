'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Check, X, Warning, ShieldCheck, Clock } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { ActionButton } from './ActionButton';
import { transitions } from '@/lib/animation-utils';

export interface ApprovalCardProps {
  /** Unique identifier */
  id: string;
  /** Title of the approval request */
  title: string;
  /** Description or details */
  description?: string;
  /** Risk level indicator */
  riskLevel?: 'low' | 'medium' | 'high' | 'critical';
  /** Items requiring approval */
  items?: Array<{
    label: string;
    value: string;
    sensitive?: boolean;
  }>;
  /** Whether approval is required before continuing */
  blocking?: boolean;
  /** Timeout in seconds (optional) */
  timeout?: number;
  /** Current approval state */
  status?: 'pending' | 'approved' | 'rejected' | 'expired';
  /** Callback when approved */
  onApprove?: () => void;
  /** Callback when rejected */
  onReject?: () => void;
  /** Show detailed view */
  showDetails?: boolean;
  /** Additional CSS classes */
  className?: string;
}

const riskConfig = {
  low: {
    color: 'text-green-500',
    bg: 'bg-green-500/10',
    border: 'border-green-500/20',
    label: 'Low Risk',
  },
  medium: {
    color: 'text-yellow-500',
    bg: 'bg-yellow-500/10',
    border: 'border-yellow-500/20',
    label: 'Medium Risk',
  },
  high: {
    color: 'text-orange-500',
    bg: 'bg-orange-500/10',
    border: 'border-orange-500/20',
    label: 'High Risk',
  },
  critical: {
    color: 'text-red-500',
    bg: 'bg-red-500/10',
    border: 'border-red-500/20',
    label: 'Critical Risk',
  },
};

/**
 * ApprovalCard - Human-in-the-loop approval interface
 *
 * Inspired by Tool UI's Approval component.
 * Features:
 * - Risk level indicators
 * - Item list with sensitive data masking
 * - Timeout countdown
 * - Approve/Reject actions
 * - Status feedback
 */
export const ApprovalCard = memo(function ApprovalCard({
  id,
  title,
  description,
  riskLevel = 'medium',
  items,
  blocking = true,
  timeout,
  status = 'pending',
  onApprove,
  onReject,
  showDetails = true,
  className,
}: ApprovalCardProps) {
  const [isProcessing, setIsProcessing] = useState(false);
  const [showAllItems, setShowAllItems] = useState(false);

  const risk = riskConfig[riskLevel];
  const isPending = status === 'pending';
  const displayItems = showAllItems ? items : items?.slice(0, 3);
  const hasMoreItems = items && items.length > 3;

  const handleApprove = useCallback(async () => {
    if (!onApprove) return;
    setIsProcessing(true);
    try {
      await onApprove();
    } finally {
      setIsProcessing(false);
    }
  }, [onApprove]);

  const handleReject = useCallback(async () => {
    if (!onReject) return;
    setIsProcessing(true);
    try {
      await onReject();
    } finally {
      setIsProcessing(false);
    }
  }, [onReject]);

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        className={cn(
          'overflow-hidden',
          risk.border,
          status === 'approved' && 'border-green-500/30 bg-green-500/5',
          status === 'rejected' && 'border-red-500/30 bg-red-500/5',
          className
        )}
      >
        {/* Header */}
        <div className="flex items-start gap-3">
          {/* Icon */}
          <div
            className={cn(
              'flex items-center justify-center w-10 h-10 rounded-lg shrink-0',
              risk.bg
            )}
          >
            {status === 'approved' ? (
              <Check className="h-5 w-5 text-green-500" weight="bold" />
            ) : status === 'rejected' ? (
              <X className="h-5 w-5 text-red-500" weight="bold" />
            ) : riskLevel === 'critical' || riskLevel === 'high' ? (
              <Warning className={cn('h-5 w-5', risk.color)} weight="fill" />
            ) : (
              <ShieldCheck className={cn('h-5 w-5', risk.color)} weight="fill" />
            )}
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h4 className="font-semibold text-sm truncate">{title}</h4>
              {blocking && isPending && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 font-medium">
                  Blocking
                </span>
              )}
            </div>

            {/* Risk badge and timeout */}
            <div className="flex items-center gap-2 text-xs">
              <span className={cn('px-1.5 py-0.5 rounded font-medium', risk.bg, risk.color)}>
                {risk.label}
              </span>
              {timeout && isPending && (
                <span className="flex items-center gap-1 text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  {timeout}s remaining
                </span>
              )}
            </div>

            {/* Description */}
            {description && (
              <p className="text-sm text-muted-foreground mt-2">{description}</p>
            )}
          </div>
        </div>

        {/* Items list */}
        {showDetails && displayItems && displayItems.length > 0 && (
          <div className="mt-4 space-y-2">
            <div className="text-xs font-medium text-muted-foreground mb-2">
              Items requiring approval
            </div>
            <div className="space-y-1.5">
              {displayItems.map((item, index) => (
                <div
                  key={index}
                  className="flex items-center justify-between py-1.5 px-2 rounded bg-muted/50 text-sm"
                >
                  <span className="text-muted-foreground">{item.label}</span>
                  <span
                    className={cn(
                      'font-mono text-xs',
                      item.sensitive && 'blur-sm hover:blur-none transition-all'
                    )}
                  >
                    {item.value}
                  </span>
                </div>
              ))}
            </div>

            {hasMoreItems && !showAllItems && (
              <button
                onClick={() => setShowAllItems(true)}
                className={cn(
                  'text-xs text-muted-foreground hover:text-foreground',
                  transitions.colors
                )}
              >
                Show {items!.length - 3} more items...
              </button>
            )}
          </div>
        )}

        {/* Actions */}
        {isPending && (onApprove || onReject) && (
          <div className="flex items-center gap-2 mt-4 pt-4 border-t border-border">
            {onReject && (
              <ActionButton
                variant="outline"
                size="sm"
                onClick={handleReject}
                disabled={isProcessing}
                icon={<X className="h-3.5 w-3.5" weight="bold" />}
              >
                Reject
              </ActionButton>
            )}
            {onApprove && (
              <ActionButton
                variant="primary"
                size="sm"
                onClick={handleApprove}
                loading={isProcessing}
                icon={!isProcessing ? <Check className="h-3.5 w-3.5" weight="bold" /> : undefined}
              >
                Approve
              </ActionButton>
            )}
          </div>
        )}

        {/* Status feedback */}
        {status === 'approved' && (
          <div className="mt-4 pt-4 border-t border-green-500/20 flex items-center gap-2 text-sm text-green-500">
            <Check className="h-4 w-4" weight="bold" />
            <span>Approved</span>
          </div>
        )}

        {status === 'rejected' && (
          <div className="mt-4 pt-4 border-t border-red-500/20 flex items-center gap-2 text-sm text-red-500">
            <X className="h-4 w-4" weight="bold" />
            <span>Rejected</span>
          </div>
        )}

        {status === 'expired' && (
          <div className="mt-4 pt-4 border-t border-muted flex items-center gap-2 text-sm text-muted-foreground">
            <Clock className="h-4 w-4" />
            <span>Expired - auto-rejected</span>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { ApprovalCardProps as ApprovalCardComponentProps };
