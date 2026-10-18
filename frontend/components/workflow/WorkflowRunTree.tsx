'use client';

import { useCallback, useRef, useState } from 'react';
import Link from 'next/link';
import { RunTree } from '@/components/run/RunTree';
import { Button } from '@/components/ui/button';
import type { WaitingNode } from '@/lib/api/workflow-logs';
import type { RunNode } from '@/lib/run/tree';

type DecisionState = { phase: 'sending' } | { phase: 'sent' } | { phase: 'error'; message: string; retryable: boolean };

export interface WorkflowRunTreeProps {
  tree: readonly RunNode[];
  /** Waiting-node info by `RunNode.id` (the nodes parked on a human decision). */
  waiting: ReadonlyMap<string, WaitingNode>;
  onDecide: (node: WaitingNode, decision: 'approve' | 'deny') => Promise<void>;
  /** Where the inbox lives; every waiting node links there so a decision can be made with full context. */
  inboxHref?: string;
  className?: string;
  'aria-label'?: string;
}

/**
 * A workflow run as a `RunTree`. Waiting nodes show their wake time (in the row) and, under it, Approve and
 * Reject controls that use the Phase 5 decision route, plus a link to the inbox. A 403 locks the controls.
 */
export function WorkflowRunTree({ tree, waiting, onDecide, inboxHref = '/inbox', className, 'aria-label': ariaLabel = 'Workflow run' }: WorkflowRunTreeProps) {
  const [state, setState] = useState<Record<string, DecisionState>>({});
  const inFlight = useRef(new Set<string>());

  const decide = useCallback(
    async (w: WaitingNode, decision: 'approve' | 'deny') => {
      if (inFlight.current.has(w.approvalId)) return;
      inFlight.current.add(w.approvalId);
      setState((s) => ({ ...s, [w.approvalId]: { phase: 'sending' } }));
      try {
        await onDecide(w, decision);
        setState((s) => ({ ...s, [w.approvalId]: { phase: 'sent' } }));
      } catch (e) {
        const message = e instanceof Error && e.message ? e.message : 'Could not send your decision. Try again.';
        const retryable = (e as { retryable?: unknown } | null)?.retryable !== false;
        setState((s) => ({ ...s, [w.approvalId]: { phase: 'error', message, retryable } }));
      } finally {
        inFlight.current.delete(w.approvalId);
      }
    },
    [onDecide]
  );

  const renderActions = useCallback(
    (node: RunNode) => {
      const w = waiting.get(node.id);
      if (!w || node.status !== 'waiting') return null;
      const st = state[w.approvalId];
      const locked = st?.phase === 'sending' || st?.phase === 'sent' || (st?.phase === 'error' && !st.retryable);
      const title = w.title || node.label;
      return (
        <div className="flex flex-wrap items-center gap-2" data-testid={`decide-${node.id}`}>
          {st?.phase === 'sent' ? (
            <span className="text-xs text-neutral-600">Decision sent. The run continues shortly.</span>
          ) : (
            <>
              <Button type="button" size="sm" disabled={locked} aria-label={`Approve ${title}`} onClick={() => void decide(w, 'approve')}>
                Approve
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={locked} aria-label={`Reject ${title}`} onClick={() => void decide(w, 'deny')}>
                Reject
              </Button>
            </>
          )}
          <Link href={inboxHref} className="text-xs text-blue-600 hover:underline">
            Decide in inbox
          </Link>
          {st?.phase === 'error' && (
            <p role="alert" className="w-full text-xs text-red-600">
              {st.message}
            </p>
          )}
        </div>
      );
    },
    [waiting, state, decide, inboxHref]
  );

  return <RunTree nodes={tree} className={className} aria-label={ariaLabel} renderActions={renderActions} />;
}
