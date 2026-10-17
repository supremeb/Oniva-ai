'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { inboxCount, listInbox, type InboxItem } from '@/lib/api/inbox';

export const INBOX_POLL_MS = 30_000;

/** Run `fn` every 30 s while the tab is visible, and on window focus. Stops on unmount. */
function useVisiblePolling(fn: () => void) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const tick = () => {
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') ref.current();
    };
    const id = setInterval(tick, INBOX_POLL_MS);
    window.addEventListener('focus', tick);
    return () => {
      clearInterval(id);
      window.removeEventListener('focus', tick);
    };
  }, []);
}

export function useInbox() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  const refetch = useCallback(async () => {
    try {
      const res = await listInbox({ limit: 200 });
      if (!alive.current) return;
      setItems(res.items);
      setError(null);
    } catch {
      if (alive.current) setError('Could not load your inbox. Try again.');
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void refetch();
    return () => {
      alive.current = false;
    };
  }, [refetch]);

  useVisiblePolling(() => void refetch());

  return { items, loading, error, refetch };
}

/** The sidebar badge's count. Errors keep the last known value. */
export function useInboxCount(): number {
  const [total, setTotal] = useState(0);
  const alive = useRef(true);
  const refetch = useCallback(async () => {
    try {
      const res = await inboxCount();
      if (alive.current) setTotal(res.total);
    } catch {
      /* keep last value */
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    void refetch();
    return () => {
      alive.current = false;
    };
  }, [refetch]);
  useVisiblePolling(() => void refetch());
  return total;
}
