"use client";

/**
 * Storage versioning utilities for automatic cache invalidation on schema changes
 *
 * When you change the structure of data stored in localStorage (e.g., user schema,
 * agent config), increment CURRENT_VERSION to trigger automatic cleanup on next load.
 */

const STORAGE_VERSION_KEY = 'oniva_storage_version';
const CURRENT_VERSION = '1.0.0'; // Increment when schema changes

export interface VersionedStorage<T> {
  version: string;
  data: T;
  timestamp: number;
}

/**
 * Check if current storage version matches expected version
 */
export function checkStorageVersion(): boolean {
  if (typeof window === 'undefined') return true;
  const storedVersion = localStorage.getItem(STORAGE_VERSION_KEY);
  return storedVersion === CURRENT_VERSION;
}

/**
 * Get current storage version
 */
export function getStorageVersion(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(STORAGE_VERSION_KEY);
}

/**
 * Get current expected version
 */
export function getCurrentVersion(): string {
  return CURRENT_VERSION;
}

/**
 * Migrate storage if version mismatch detected
 * Call this on app load to automatically clean up stale data
 */
export function migrateStorageIfNeeded(): void {
  if (typeof window === 'undefined') return;

  const storedVersion = localStorage.getItem(STORAGE_VERSION_KEY);

  if (storedVersion === CURRENT_VERSION) {
    // Version matches, no migration needed
    return;
  }

  console.warn(
    `[Storage] Version mismatch detected. Stored: ${storedVersion || 'none'}, Current: ${CURRENT_VERSION}`
  );
  console.log('[Storage] Clearing stale data...');

  // Keys to always preserve (auth tokens should survive version changes)
  const keysToPreserve = ['access_token', 'auth_user', STORAGE_VERSION_KEY];

  // Get all keys that should be removed
  const keysToRemove = Object.keys(localStorage).filter(
    key => !keysToPreserve.includes(key)
  );

  // Remove stale keys
  keysToRemove.forEach(key => {
    console.log(`[Storage] Removing stale key: ${key}`);
    localStorage.removeItem(key);
  });

  // Update version stamp
  localStorage.setItem(STORAGE_VERSION_KEY, CURRENT_VERSION);

  console.log(`[Storage] Migration complete. Removed ${keysToRemove.length} stale keys.`);
}

/**
 * Store data with version stamp
 * Use this for any localStorage data that might have schema changes
 */
export function setVersionedItem<T>(key: string, data: T): void {
  if (typeof window === 'undefined') return;

  const versioned: VersionedStorage<T> = {
    version: CURRENT_VERSION,
    data,
    timestamp: Date.now(),
  };

  localStorage.setItem(key, JSON.stringify(versioned));
}

/**
 * Retrieve versioned data, returning null if version mismatch
 * Automatically cleans up stale data
 */
export function getVersionedItem<T>(key: string): T | null {
  if (typeof window === 'undefined') return null;

  const raw = localStorage.getItem(key);
  if (!raw) return null;

  try {
    const versioned = JSON.parse(raw) as VersionedStorage<T>;

    // Check if data has version field (backwards compatibility)
    if (!versioned.version) {
      // Old format without version - treat as stale
      console.warn(`[Storage] Found unversioned data for "${key}", removing`);
      localStorage.removeItem(key);
      return null;
    }

    // Check version match
    if (versioned.version !== CURRENT_VERSION) {
      console.warn(
        `[Storage] Version mismatch for "${key}": ${versioned.version} !== ${CURRENT_VERSION}, removing`
      );
      localStorage.removeItem(key);
      return null;
    }

    return versioned.data;
  } catch (e) {
    // Invalid JSON - remove corrupted data
    console.warn(`[Storage] Invalid data for "${key}", removing:`, e);
    localStorage.removeItem(key);
    return null;
  }
}

/**
 * Remove a versioned item
 */
export function removeVersionedItem(key: string): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(key);
}

/**
 * Check if a versioned item exists and is valid
 */
export function hasValidVersionedItem(key: string): boolean {
  return getVersionedItem(key) !== null;
}

/**
 * Get the age of a versioned item in milliseconds
 * Returns null if item doesn't exist or is invalid
 */
export function getVersionedItemAge(key: string): number | null {
  if (typeof window === 'undefined') return null;

  const raw = localStorage.getItem(key);
  if (!raw) return null;

  try {
    const versioned = JSON.parse(raw) as VersionedStorage<unknown>;
    if (!versioned.timestamp || versioned.version !== CURRENT_VERSION) {
      return null;
    }
    return Date.now() - versioned.timestamp;
  } catch {
    return null;
  }
}

// Export version for external checks
export { CURRENT_VERSION as STORAGE_VERSION };
