'use client';

import { useEffect } from 'react';
import { migrateStorageIfNeeded } from '@/lib/utils/storage-versioning';

/**
 * Storage Migration Provider
 *
 * Runs on app load to check for storage version mismatches and
 * automatically clean up stale localStorage data.
 *
 * This prevents "works in incognito but not regular browser" issues
 * caused by outdated cached data after schema changes.
 */
export function StorageMigrationProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  useEffect(() => {
    // Run storage migration check on mount
    migrateStorageIfNeeded();
  }, []);

  return <>{children}</>;
}
