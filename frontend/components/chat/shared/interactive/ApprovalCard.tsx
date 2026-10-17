'use client';

import { memo, useState, useCallback, useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import { Check, X, Warning, ShieldCheck, Clock } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { ActionButton } from './ActionButton';

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
  /** Short hash of the canonical arguments. */
  argumentsHash?: string;
  /** Whether approval is required before continuing */
  blocking?: boolean;
  /** Timeout in seconds (optional) */
  timeout?: number;
  /** Current approval state */
  status?: 'pending' | 'approved' | 'rejected' | 'expired';
  /**
   * Callbacks. A rejected promise shows its message inline and re-enables the
   * buttons, unless the error has `retryable === false` (the card then stays
   * locked). On success the buttons stay disabled until `status` changes.
   */
  onApprove?: () => void | Promise<void>;
  onReject?: () => void | Promise<void>;
  /** Engine approvals: tool the request is for (shown in the resolved line). */
  toolName?: string;
  /** Epoch ms; a pending card past it renders as expired. */
  expiresAt?: number | null;
  /** Decision note. */
  note?: string | null;
  /** The viewer cannot decide: no buttons. */
  readOnly?: boolean;
  /** Render a decided request as a compact transcript line instead of a card. */
  resolvedAsLine?: boolean;
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
  argumentsHash,
  blocking = true,
  timeout,
  status: statusProp = 'pending',
  onApprove,
  onReject,
  toolName,
  expiresAt,
  note,
  readOnly = false,
  resolvedAsLine = false,
  showDetails = true,
  className,
}: ApprovalCardProps) {
  const [submitted, setSubmitted] = useState<'approve' | 'reject' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const inFlight = useRef(false);

  const expired = statusProp === 'pending' && expiresAt != null && now >= expiresAt;
  const status = expired ? 'expired' : statusProp;
  useEffect(() => {
    if (statusProp !== 'pending' || expiresAt == null) return;
    const wait = expiresAt - Date.now();
    if (wait <= 0) {
      setNow(Date.now());
      return;
    }
    const t = setTimeout(() => setNow(Date.now()), wait);
    return () => clearTimeout(t);
  }, [statusProp, expiresAt]);

  const risk = riskConfig[riskLevel];
  const isPending = status === 'pending';
  // Every argument is shown in full: an approver must never decide on a truncated view.
  const displayItems = items;

  const decide = useCallback(async (kind: 'approve' | 'reject', fn: (() => void | Promise<void>) | undefined) => {
    if (!fn || inFlight.current) return; // a second click while a decision is in flight is ignored
    inFlight.current = true;
    setSubmitted(kind);
    setError(null);
    try {
      await fn();
    } catch (e) {
      inFlight.current = false;
      setSubmitted(null);
      setError(e instanceof Error && e.message ? e.message : 'Could not send your decision.');
      if ((e as { retryable?: boolean } | null)?.retryable === false) setLocked(true);
    }
  }, []);
  const handleApprove = useCallback(() => decide('approve', onApprove), [decide, onApprove]);
  const handleReject = useCallback(() => decide('reject', onReject), [decide, onReject]);
  const busy = submitted !== null || locked;

  if (resolvedAsLine && !isPending) {
    const label = status === 'approved' ? 'Approved' : status === 'rejected' ? 'Denied' : 'Expired, not run';
    return (
      <div
        role="status"
        data-testid="approval-resolved"
        data-status={status}
        className={cn('flex items-center gap-2 text-sm text-muted-foreground', className)}
      >
        {status === 'approved' ? (
          <Check className="h-4 w-4 text-green-500" weight="bold" />
        ) : status === 'rejected' ? (
          <X className="h-4 w-4 text-red-500" weight="bold" />
        ) : (
          <Clock className="h-4 w-4" />
        )}
        <span>
          {label}
          {toolName ? `: ${toolName}` : ''}
          {note ? ` (${note})` : ''}
        </span>
      </div>
    );
  }

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
            <div
              data-testid="approval-arguments"
              className="space-y-1.5 max-h-80 overflow-auto"
              tabIndex={0}
              role="group"
              aria-label="Full arguments"
            >
              {displayItems.map((item, index) => (
                <div
                  key={index}
                  className="flex flex-col gap-0.5 py-1.5 px-2 rounded bg-muted/50 text-sm"
                >
                  <span className="text-muted-foreground">{item.label}</span>
                  <pre
                    className={cn(
                      'font-mono text-xs whitespace-pre-wrap break-all m-0',
                      item.sensitive && 'blur-sm hover:blur-none transition-all'
                    )}
                  >
                    {item.value}
                  </pre>
                </div>
              ))}
            </div>
            {argumentsHash && (
              <div className="text-xs text-muted-foreground">
                Arguments hash: <code className="font-mono">{argumentsHash}</code>
              </div>
            )}
          </div>
        )}

        {/* Actions */}
        {isPending && readOnly && (
          <p className="mt-4 pt-4 border-t border-border text-xs text-muted-foreground">
            Waiting for the conversation owner or an admin to decide.
          </p>
        )}
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-500">
            {error}
          </p>
        )}
        {isPending && !readOnly && (onApprove || onReject) && (
          <div className="flex items-center gap-2 mt-4 pt-4 border-t border-border">
            {onReject && (
              <ActionButton
                variant="outline"
                size="sm"
                onClick={handleReject}
                disabled={busy}
                loading={submitted === 'reject'}
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
                disabled={busy}
                loading={submitted === 'approve'}
                icon={submitted !== 'approve' ? <Check className="h-3.5 w-3.5" weight="bold" /> : undefined}
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
            <span>Expired, auto-denied</span>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { ApprovalCardProps as ApprovalCardComponentProps };
