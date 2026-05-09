/**
 * Per-tenant feature flag hook.
 *
 * Reads resolved features from the auth store (populated by /auth/me).
 * IMPORTANT: Call unconditionally at the top of components — never inside
 * conditionals or after early returns (React hooks rules).
 *
 * @see docs/solutions/ui-bugs/react-hooks-order-DeepWorkInlineCard-20260216.md
 */
import { useAuthStore } from '@/lib/stores/auth';

/**
 * Check if a feature flag is enabled for the current tenant.
 *
 * @param flagName - The feature flag name (e.g., "ENABLE_COMPLIANCE")
 * @returns true if the feature is enabled, false otherwise
 */
export function useFeature(flagName: string): boolean {
  const user = useAuthStore((state) => state.user);
  return user?.features?.[flagName] ?? false;
}

/**
 * Check if the current user is a platform operator.
 */
export function useIsPlatformOperator(): boolean {
  const user = useAuthStore((state) => state.user);
  return user?.is_platform_operator ?? false;
}

/**
 * Get all resolved features for the current tenant.
 */
export function useFeatures(): Record<string, boolean> {
  const user = useAuthStore((state) => state.user);
  return user?.features ?? {};
}
