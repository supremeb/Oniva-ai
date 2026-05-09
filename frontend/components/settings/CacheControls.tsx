'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { ArrowCounterClockwise, Database, SignOut, Trash } from '@phosphor-icons/react';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import {
  clearAllCache,
  clearAuthAndRefresh,
  getCacheDebugInfo,
} from '@/lib/utils/cache-debug';
import {
  invalidateAllQueries,
  getQueryCacheStats,
} from '@/lib/utils/query-invalidation';
import {
  getStorageVersion,
  getCurrentVersion,
} from '@/lib/utils/storage-versioning';

/**
 * Cache Controls component for the Settings page
 *
 * Provides user-facing controls to diagnose and fix cache issues,
 * particularly "works in incognito but not regular browser" scenarios.
 */
export function CacheControls() {
  const queryClient = useQueryClient();
  const [showDebug, setShowDebug] = useState(false);
  const [showClearAllDialog, setShowClearAllDialog] = useState(false);

  const handleRefreshQueries = () => {
    invalidateAllQueries(queryClient);
  };

  const handleClearCache = () => {
    clearAllCache();
  };

  const handleClearAll = () => {
    setShowClearAllDialog(true);
  };

  const confirmClearAll = async () => {
    clearAuthAndRefresh();
  };

  // Get debug info only when debug panel is shown
  const debugInfo = showDebug
    ? {
        cache: getCacheDebugInfo(),
        queryStats: getQueryCacheStats(queryClient),
        storageVersion: getStorageVersion(),
        currentVersion: getCurrentVersion(),
      }
    : null;

  return (
    <>
    <Card>
      <CardHeader>
        <Database className="h-8 w-8 mb-2 text-primary" />
        <CardTitle>Cache & Storage</CardTitle>
        <CardDescription>
          Clear cached data if you&apos;re experiencing issues with stale or
          incorrect data
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={handleRefreshQueries}>
            <ArrowCounterClockwise className="mr-2 h-4 w-4" />
            Refresh Data
          </Button>
          <Button variant="outline" onClick={handleClearCache}>
            <Trash className="mr-2 h-4 w-4" />
            Clear Cache
          </Button>
          <Button variant="destructive" onClick={handleClearAll}>
            <SignOut className="mr-2 h-4 w-4" />
            Clear All & Re-login
          </Button>
        </div>

        <p className="text-sm text-muted-foreground">
          <strong>Refresh Data</strong>: Refetches all queries from the server.
          <br />
          <strong>Clear Cache</strong>: Clears stored data but keeps you logged
          in.
          <br />
          <strong>Clear All</strong>: Removes everything including
          authentication.
        </p>

        {/* Debug toggle */}
        <div className="pt-4 border-t">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowDebug(!showDebug)}
          >
            {showDebug ? 'Hide' : 'Show'} Debug Info
          </Button>

          {showDebug && debugInfo && (
            <div className="mt-4 p-4 bg-muted rounded-lg text-xs font-mono space-y-3">
              <div>
                <strong>Storage Version:</strong> {debugInfo.storageVersion}{' '}
                (current: {debugInfo.currentVersion})
              </div>
              <div>
                <strong>Query Cache:</strong>
                <ul className="ml-4 mt-1">
                  <li>Total queries: {debugInfo.queryStats.totalQueries}</li>
                  <li>Active: {debugInfo.queryStats.activeQueries}</li>
                  <li>Stale: {debugInfo.queryStats.staleQueries}</li>
                </ul>
              </div>
              <div>
                <strong>LocalStorage:</strong> {debugInfo.cache.localStorage.length} items (
                {Math.round(debugInfo.cache.totalLocalStorageSize / 1024)} KB)
                <ul className="ml-4 mt-1 max-h-32 overflow-y-auto">
                  {debugInfo.cache.localStorage.map((item) => (
                    <li key={item.key} className="truncate">
                      {item.key}: {item.size} bytes
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <strong>SessionStorage:</strong> {debugInfo.cache.sessionStorage.length} items (
                {Math.round(debugInfo.cache.totalSessionStorageSize / 1024)} KB)
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>

    <ConfirmationDialog
      open={showClearAllDialog}
      onOpenChange={setShowClearAllDialog}
      title="Clear All Cache"
      description="This will clear all cached data and log you out. Continue?"
      confirmLabel="Clear All"
      onConfirm={confirmClearAll}
    />
    </>
  );
}
