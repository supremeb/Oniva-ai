'use client';

import { useMemo, useState } from 'react';
import { EngineConversationClient } from '@/lib/engine/client';
import { toDecisionError } from '@/lib/engine/approval';
import { INBOX_KINDS, type InboxItem, type InboxKind } from '@/lib/api/inbox';
import { InboxItemRow, KIND_LABEL, type RowState } from './InboxItemRow';

const client = new EngineConversationClient();

const DECIDE_PATH = /\/conversations\/([^/]+)\/approvals\/([^/]+)\/decision$/;

function parseDecide(endpoint?: string): { conversationId: string; toolTaskId: string } | null {
  const m = endpoint ? DECIDE_PATH.exec(endpoint) : null;
  return m ? { conversationId: decodeURIComponent(m[1]), toolTaskId: decodeURIComponent(m[2]) } : null;
}

interface Props {
  items: InboxItem[];
  /** Called after a decision succeeds so the caller can refetch. */
  onDecided?: () => void;
}

export function InboxList({ items, onDecided }: Props) {
  const [filter, setFilter] = useState<InboxKind | null>(null);
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [rows, setRows] = useState<Record<string, RowState>>({});

  const visible = useMemo(() => items.filter((i) => !removed.has(i.id)), [items, removed]);
  const present = useMemo(() => INBOX_KINDS.filter((k) => visible.some((i) => i.kind === k)), [visible]);
  const shown = filter ? visible.filter((i) => i.kind === filter) : visible;

  const setRow = (id: string, s: RowState) => setRows((r) => ({ ...r, [id]: s }));

  const onDecide = async (item: InboxItem, decision: 'approve' | 'deny') => {
    const target = parseDecide(item.actions.find((a) => a.type === 'decide')?.endpoint);
    if (!target) return;
    setRow(item.id, { pending: true });
    try {
      await client.decide(target.conversationId, target.toolTaskId, decision);
      setRemoved((s) => new Set(s).add(item.id));
      onDecided?.();
    } catch (e) {
      setRow(item.id, { error: toDecisionError(e).message });
    }
  };

  return (
    <div>
      {present.length > 1 && (
        <div role="group" aria-label="Filter by kind" className="flex flex-wrap gap-2 px-4 py-3">
          <button
            type="button"
            aria-pressed={filter === null}
            onClick={() => setFilter(null)}
            className="rounded-full border border-border px-3 py-1 text-xs aria-pressed:bg-primary/10"
          >
            All
          </button>
          {present.map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={filter === k}
              onClick={() => setFilter(k)}
              className="rounded-full border border-border px-3 py-1 text-xs aria-pressed:bg-primary/10"
            >
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>
      )}
      {shown.length === 0 ? (
        <p className="px-4 py-12 text-center text-sm text-muted-foreground">Nothing needs your attention.</p>
      ) : (
        <ul aria-label="Needs attention">
          {shown.map((item) => (
            <InboxItemRow key={item.id} item={item} state={rows[item.id]} onDecide={onDecide} />
          ))}
        </ul>
      )}
    </div>
  );
}
