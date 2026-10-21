/**
 * Project memory state: loads the overview and runs each mutation then reloads, so the UI always
 * shows server truth. Errors are classified so the view can react: 403 -> read-only,
 * 409 -> "reload" prompt (stale version or already decided), 404 -> feature off / gone.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { APIError } from '@/lib/api';
import {
  fetchProjectMemory,
  putMemoryFile,
  deleteMemoryFile,
  deleteMemoryBullet,
  approvePendingMemory,
  rejectPendingMemory,
  exportProjectMemory,
  purgeProjectMemory,
  type MemoryOverview,
  type MemoryExport,
} from '@/lib/api/project-memory';

export type MemoryErrorKind = 'forbidden' | 'conflict' | 'not_found' | 'error';

export interface MemoryError {
  kind: MemoryErrorKind;
  message: string;
}

function classify(e: unknown): MemoryError {
  if (e instanceof APIError) {
    if (e.status === 403) return { kind: 'forbidden', message: 'You do not have permission to change project memory.' };
    if (e.status === 409)
      return { kind: 'conflict', message: 'This memory changed since you loaded it. Reload to see the latest.' };
    if (e.status === 404) return { kind: 'not_found', message: 'Project memory is not available.' };
    return { kind: 'error', message: e.message };
  }
  return { kind: 'error', message: e instanceof Error ? e.message : 'Something went wrong.' };
}

export function useProjectMemory(projectId: string, enabled = true) {
  const [data, setData] = useState<MemoryOverview | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<MemoryError | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const overview = await fetchProjectMemory(projectId);
      if (!alive.current) return;
      setData(overview);
      setError(null);
    } catch (e) {
      if (alive.current) setError(classify(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (enabled) void reload();
  }, [enabled, reload]);

  /** Run a mutation then reload. Returns true on success. */
  const mutate = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
      setBusy(true);
      setError(null);
      try {
        const result = await fn();
        await reload();
        return result;
      } catch (e) {
        const err = classify(e);
        if (alive.current) {
          setError(err);
          if (err.kind === 'forbidden') setReadOnly(true);
        }
        return undefined;
      } finally {
        if (alive.current) setBusy(false);
      }
    },
    [reload]
  );

  const saveFile = (path: string, content: string, expectedVersion?: number) =>
    mutate(() => putMemoryFile(projectId, path, content, expectedVersion));
  const deleteFile = (path: string, expectedVersion?: number) =>
    mutate(() => deleteMemoryFile(projectId, path, expectedVersion));
  const deleteBullet = (id: string) => mutate(() => deleteMemoryBullet(projectId, id));
  const approve = (id: string) => mutate(() => approvePendingMemory(projectId, id));
  const reject = (id: string) => mutate(() => rejectPendingMemory(projectId, id));
  const purge = async () => {
    const r = await mutate(() => purgeProjectMemory(projectId));
    if (r === undefined) throw new Error('purge failed'); // keeps the confirm dialog open
    return r;
  };
  const exportAll = async (): Promise<MemoryExport | undefined> => {
    try {
      return await exportProjectMemory(projectId);
    } catch (e) {
      setError(classify(e));
      return undefined;
    }
  };

  return {
    data,
    loading,
    error,
    readOnly,
    busy,
    reload,
    clearError: () => setError(null),
    saveFile,
    deleteFile,
    deleteBullet,
    approve,
    reject,
    purge,
    exportAll,
  };
}
