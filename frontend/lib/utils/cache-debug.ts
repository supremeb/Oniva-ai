"use client";

/**
 * Cache debugging utilities for diagnosing stale data issues
 *
 * Use these utilities when users report "works in incognito but not regular browser"
 */

export interface CacheDebugInfo {
  localStorage: { key: string; size: number; preview: string }[];
  sessionStorage: { key: string; size: number; preview: string }[];
  totalLocalStorageSize: number;
  totalSessionStorageSize: number;
}

/**
 * Get information about all cached data in browser storage
 */
export function getCacheDebugInfo(): CacheDebugInfo {
  if (typeof window === 'undefined') {
    return {
      localStorage: [],
      sessionStorage: [],
      totalLocalStorageSize: 0,
      totalSessionStorageSize: 0,
    };
  }

  const localStorageData = Object.keys(localStorage).map(key => {
    const value = localStorage.getItem(key) || '';
    return {
      key,
      size: value.length,
      preview: value.slice(0, 100) + (value.length > 100 ? '...' : ''),
    };
  });

  const sessionStorageData = Object.keys(sessionStorage).map(key => {
    const value = sessionStorage.getItem(key) || '';
    return {
      key,
      size: value.length,
      preview: value.slice(0, 100) + (value.length > 100 ? '...' : ''),
    };
  });

  return {
    localStorage: localStorageData,
    sessionStorage: sessionStorageData,
    totalLocalStorageSize: localStorageData.reduce((acc, item) => acc + item.size, 0),
    totalSessionStorageSize: sessionStorageData.reduce((acc, item) => acc + item.size, 0),
  };
}

/**
 * Log cache debug info to console (for development)
 */
export function logCacheDebugInfo(): void {
  const info = getCacheDebugInfo();
  console.group('[Cache Debug Info]');
  console.log('localStorage items:', info.localStorage.length);
  console.log('localStorage total size:', formatBytes(info.totalLocalStorageSize));
  console.table(info.localStorage);
  console.log('sessionStorage items:', info.sessionStorage.length);
  console.log('sessionStorage total size:', formatBytes(info.totalSessionStorageSize));
  console.table(info.sessionStorage);
  console.groupEnd();
}

/**
 * Clear all cached data except auth credentials
 * Useful for fixing stale data issues without forcing re-login
 */
export function clearAllCache(): void {
  if (typeof window === 'undefined') return;

  // Keys to preserve (auth-related)
  const preserveKeys = ['access_token', 'auth_user'];

  // Clear localStorage except preserved keys
  Object.keys(localStorage).forEach(key => {
    if (!preserveKeys.includes(key)) {
      localStorage.removeItem(key);
    }
  });

  // Clear all sessionStorage
  sessionStorage.clear();

  console.log('[Cache] Cleared all cache except auth credentials');

  // Reload to clear in-memory caches (React Query, Zustand)
  window.location.reload();
}

/**
 * Clear everything including auth and redirect to login
 * Nuclear option for persistent issues
 */
export function clearAuthAndRefresh(): void {
  if (typeof window === 'undefined') return;

  // Clear all localStorage
  localStorage.clear();

  // Clear all sessionStorage
  sessionStorage.clear();

  console.log('[Cache] Cleared all storage including auth');

  // Redirect to login
  window.location.href = '/login';
}

/**
 * Clear specific cache keys by prefix
 */
export function clearCacheByPrefix(prefix: string): void {
  if (typeof window === 'undefined') return;

  const keysToRemove = Object.keys(localStorage).filter(key => key.startsWith(prefix));
  keysToRemove.forEach(key => localStorage.removeItem(key));

  console.log(`[Cache] Cleared ${keysToRemove.length} keys with prefix "${prefix}"`);
}

/**
 * Format bytes to human readable string
 */
function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Expose to window for debugging in dev tools
if (typeof window !== 'undefined' && process.env.NODE_ENV === 'development') {
  const win = window as unknown as Record<string, unknown>;
  win.getCacheDebugInfo = getCacheDebugInfo;
  win.clearAllCache = clearAllCache;
  win.logCacheDebugInfo = logCacheDebugInfo;
}
