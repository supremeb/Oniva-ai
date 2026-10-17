'use client';

import { InboxList } from '@/components/inbox/InboxList';
import { useInbox } from '@/hooks/useInbox';

export default function InboxPage() {
  const { items, loading, error, refetch } = useInbox();
  return (
    <div className="mx-auto max-w-3xl py-6">
      <h1 className="px-4 text-xl font-semibold">Needs attention</h1>
      {loading ? (
        <p className="px-4 py-12 text-sm text-muted-foreground">Loading...</p>
      ) : error && items.length === 0 ? (
        <div role="alert" className="px-4 py-12 text-sm text-destructive">
          {error}{' '}
          <button type="button" className="underline" onClick={() => void refetch()}>
            Retry
          </button>
        </div>
      ) : (
        <InboxList items={items} onDecided={() => void refetch()} />
      )}
    </div>
  );
}
