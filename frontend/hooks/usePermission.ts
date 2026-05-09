/**
 * Permission hooks for role-based access control
 */
import { useAuthStore } from '@/lib/stores/auth';

// =========================================
// Permission Definitions (must match backend)
// =========================================

const ROLE_PERMISSIONS: Record<string, Set<string>> = {
  owner: new Set(['*']), // Full access
  admin: new Set([
    // Workflow permissions
    'workflows:create',
    'workflows:read',
    'workflows:update',
    'workflows:delete',
    'workflows:execute',
    // Agent permissions
    'agents:create',
    'agents:read',
    'agents:update',
    'agents:delete',
    'agents:execute',
    // User management
    'users:read',
    'users:manage',
    // Settings
    'settings:read',
    'settings:manage',
    // Connectors
    'connectors:read',
    'connectors:manage',
    // API Keys
    'api_keys:read',
    'api_keys:manage',
  ]),
  member: new Set([
    // Workflow permissions
    'workflows:create',
    'workflows:read',
    'workflows:update',
    'workflows:execute',
    'workflows:delete_own',
    // Agent permissions
    'agents:create',
    'agents:read',
    'agents:update',
    'agents:execute',
    'agents:delete_own',
    // Limited settings
    'settings:read',
    // Limited API keys
    'api_keys:read',
    'api_keys:manage_own',
  ]),
  viewer: new Set([
    // Read-only access
    'workflows:read',
    'agents:read',
    'settings:read',
  ]),
};

// =========================================
// Permission Checking
// =========================================

/**
 * Check if a role has a specific permission
 */
function hasPermission(role: string, permission: string): boolean {
  const perms = ROLE_PERMISSIONS[role];
  if (!perms) return false;

  // Owner has all permissions
  if (perms.has('*')) return true;

  // Direct permission match
  if (perms.has(permission)) return true;

  // Wildcard permission (e.g., "workflows:*" matches "workflows:create")
  const resource = permission.split(':')[0];
  if (perms.has(`${resource}:*`)) return true;

  return false;
}

/**
 * Check if role can manage users with target role
 */
function canManageRole(actorRole: string, targetRole: string): boolean {
  if (actorRole === 'owner') return true;
  if (actorRole === 'admin') {
    return targetRole === 'member' || targetRole === 'viewer';
  }
  return false;
}

// =========================================
// React Hooks
// =========================================

/**
 * Hook to check a single permission
 */
export function usePermission(permission: string): boolean {
  const user = useAuthStore((state) => state.user);
  if (!user?.role) return false;
  return hasPermission(user.role, permission);
}

/**
 * Hook to check any of multiple permissions
 */
export function useAnyPermission(...permissions: string[]): boolean {
  const user = useAuthStore((state) => state.user);
  if (!user?.role) return false;
  return permissions.some((p) => hasPermission(user.role, p));
}

/**
 * Hook to check all permissions
 */
export function useAllPermissions(...permissions: string[]): boolean {
  const user = useAuthStore((state) => state.user);
  if (!user?.role) return false;
  return permissions.every((p) => hasPermission(user.role, p));
}

/**
 * Hook to check if current user can manage another user's role
 */
export function useCanManageRole(targetRole: string): boolean {
  const user = useAuthStore((state) => state.user);
  if (!user?.role) return false;
  return canManageRole(user.role, targetRole);
}

/**
 * Hook to get all permission-related utilities
 */
export function usePermissions() {
  const user = useAuthStore((state) => state.user);
  const role = user?.role || 'viewer';

  return {
    role,
    isOwner: role === 'owner',
    isAdmin: role === 'owner' || role === 'admin',
    isMember: role === 'owner' || role === 'admin' || role === 'member',
    canManageUsers: role === 'owner' || role === 'admin',
    hasPermission: (permission: string) => hasPermission(role, permission),
    hasAnyPermission: (...permissions: string[]) =>
      permissions.some((p) => hasPermission(role, p)),
    hasAllPermissions: (...permissions: string[]) =>
      permissions.every((p) => hasPermission(role, p)),
    canManageRole: (targetRole: string) => canManageRole(role, targetRole),
  };
}

// =========================================
// Exports
// =========================================

export { hasPermission, canManageRole };
