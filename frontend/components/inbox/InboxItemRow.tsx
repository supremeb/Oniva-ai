'use client';

import Link from 'next/link';
import { Button } from '@/components/ui/button';
import type { InboxItem, InboxKind } from '@/lib/api/inbox';

export const KIND_LABEL: Record<InboxKind, string> = {
  approval: 'Approval',
  review: 'Review',
  question: 'Question',
  compliance: 'Compliance',
  fleet: 'Fleet',
  deep_work: 'Deep Work',
};

export interface RowState {
  pending?: boolean;
  error?: string;
}

interface Props {
  item: InboxItem;
  state?: RowState;
  onDecide: (item: InboxItem, decision: 'approve' | 'deny') => void;
}

const DISABLED_COPY: Record<string, string> = {
  self_approval_forbidden: 'Another admin must approve this.',
};

export function InboxItemRow({ item, state, onDecide }: Props) {
  const decide = item.actions.find((a) => a.type === 'decide');
  const linkLabel = item.kind === 'question' ? 'Answer in chat' : 'Open';
  const disabled = decide && !decide.allowed;
  return (
    <li className="flex items-start justify-between gap-4 border-b border-border px-4 py-3" data-kind={item.kind}>
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>{KIND_LABEL[item.kind] ?? item.kind}</span>
          {item.severity === 'high' && <span className="text-destructive">High priority</span>}
        </div>
        <p className="text-sm font-medium text-foreground">{item.title}</p>
        {item.summary && <p className="text-sm text-muted-foreground">{item.summary}</p>}
        {state?.error && (
          <p role="alert" className="mt-1 text-sm text-destructive">
            {state.error}
          </p>
        )}
        {disabled && (
          <p className="mt-1 text-xs text-muted-foreground">
            {DISABLED_COPY[decide.disabledReason ?? ''] ?? 'You cannot decide this.'}
          </p>
        )}
      </div>
      <div role="group" aria-label={`Actions for ${item.title}`} className="flex shrink-0 items-center gap-2">
        {decide && (
          <>
            <Button
              size="sm"
              disabled={!decide.allowed || state?.pending}
              onClick={() => onDecide(item, 'approve')}
            >
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!decide.allowed || state?.pending}
              onClick={() => onDecide(item, 'deny')}
            >
              Deny
            </Button>
          </>
        )}
        <Link href={item.href} className="text-sm text-primary hover:underline">
          {linkLabel}
        </Link>
      </div>
    </li>
  );
}
