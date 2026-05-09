/**
 * Tenant-aware localStorage utilities
 *
 * SECURITY: All localStorage keys for tenant-specific data must be prefixed
 * with the tenant ID to prevent cross-tenant data leakage when users
 * switch between tenants in the same browser.
 *
 * - Phase 1: Security Fixes
 */

/**
 * Get the current tenant ID from stored auth user.
 * Returns null if no user is logged in or tenant_id is missing.
 */
export function getCurrentTenantId(): string | null {
  if (typeof window === 'undefined') return null;

  try {
    const userStr = localStorage.getItem('auth_user');
    if (!userStr) return null;
    const user = JSON.parse(userStr);
    return user.tenant_id || null;
  } catch {
    return null;
  }
}

/**
 * Get a tenant-prefixed localStorage key.
 * Falls back to '-guest' suffix if no tenant is logged in.
 *
 * @param baseKey - The base key name (e.g., 'oniva-workflows')
 * @returns Tenant-prefixed key (e.g., 'oniva-workflows-abc123')
 */
export function getTenantKey(baseKey: string): string {
  const tenantId = getCurrentTenantId();
  return tenantId ? `${baseKey}-${tenantId}` : `${baseKey}-guest`;
}

/**
 * Legacy key names that need migration to tenant-prefixed versions.
 * Used by migrateLocalStorage() in auth store.
 */
export const LEGACY_STORAGE_KEYS = [
  'oniva-workflows',
  'oniva-custom',
  'oniva-versions',
  'oniva-chat-sessions',
  'oniva-chat-panel',
  'oniva-chat',
  'oniva-ui',
  'oniva:bottom-panel:v2',
  'oniva:agent-studio-layout:v1',
  'agent_id_migration_map',
] as const;

/**
 * Migrate legacy localStorage data to tenant-prefixed keys.
 * Called on login to preserve user data while adding tenant isolation.
 *
 * Strategy:
 * 1. Check if legacy key exists
 * 2. Check if tenant-prefixed key already exists (don't overwrite)
 * 3. Copy legacy data to tenant-prefixed key
 * 4. Remove legacy key to prevent future confusion
 *
 * @param tenantId - The tenant ID to migrate data to
 */
export function migrateLocalStorage(tenantId: string): void {
  if (typeof window === 'undefined' || !tenantId) return;

  for (const legacyKey of LEGACY_STORAGE_KEYS) {
    try {
      const legacyData = localStorage.getItem(legacyKey);
      if (!legacyData) continue;

      const newKey = `${legacyKey}-${tenantId}`;

      // Don't overwrite existing tenant-prefixed data
      if (!localStorage.getItem(newKey)) {
        localStorage.setItem(newKey, legacyData);
        console.log(`[TenantStorage] Migrated ${legacyKey} -> ${newKey}`);
      }

      // Remove legacy key to prevent cross-tenant leakage
      localStorage.removeItem(legacyKey);
    } catch (error) {
      console.error(`[TenantStorage] Failed to migrate ${legacyKey}:`, error);
    }
  }

  // Migrate workflow_chat_* keys (dynamic keys based on workflow ID)
  migrateWorkflowChatKeys(tenantId);
}

/**
 * Migrate workflow chat history keys to tenant-prefixed versions.
 * These keys follow the pattern: workflow_chat_{workflowId}
 */
function migrateWorkflowChatKeys(tenantId: string): void {
  const keysToMigrate: string[] = [];

  // Find all workflow_chat_ keys without tenant prefix
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith('workflow_chat_') && !key.includes(`-${tenantId}`)) {
      // Check if this is a legacy key (doesn't have a tenant suffix pattern)
      // Tenant IDs are UUIDs, so check if key doesn't end with UUID pattern
      const uuidPattern = /-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
      if (!uuidPattern.test(key)) {
        keysToMigrate.push(key);
      }
    }
  }

  for (const legacyKey of keysToMigrate) {
    try {
      const legacyData = localStorage.getItem(legacyKey);
      if (!legacyData) continue;

      const newKey = `${legacyKey}-${tenantId}`;

      if (!localStorage.getItem(newKey)) {
        localStorage.setItem(newKey, legacyData);
        console.log(`[TenantStorage] Migrated ${legacyKey} -> ${newKey}`);
      }

      localStorage.removeItem(legacyKey);
    } catch (error) {
      console.error(`[TenantStorage] Failed to migrate ${legacyKey}:`, error);
    }
  }
}

/**
 * Clear all tenant-specific localStorage data for a given tenant.
 * Used on logout or tenant switch to prevent data leakage.
 *
 * @param tenantId - The tenant ID to clear data for
 */
export function clearTenantData(tenantId: string): void {
  if (typeof window === 'undefined' || !tenantId) return;

  const suffix = `-${tenantId}`;
  const keysToRemove: string[] = [];

  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.endsWith(suffix)) {
      keysToRemove.push(key);
    }
  }

  for (const key of keysToRemove) {
    localStorage.removeItem(key);
  }

  console.log(`[TenantStorage] Cleared ${keysToRemove.length} keys for tenant ${tenantId}`);
}
