/**
 * Per-tenant feature flag hook.
 *
 * Reads resolved features from the auth store (populated by /auth/me).
 * IMPORTANT: Call unconditionally at the top of components — never inside
 * conditionals or after early returns (React hooks rules).
 *
 * @see docs/solutions/ui-bugs/react-hooks-order-DeepWorkInlineCard-20260216.md
 */
import { useState } from 'react';
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

/** Feature flag that selects chat on the engine (resolved server-side per tenant). */
export const ENGINE_CHAT_FLAG = 'ENABLE_ENGINE_CHAT';

/**
 * Chat mode for this mount. The server decides; the value is captured once per mount so a
 * flag flip while the page is open never switches mode mid-conversation (reload picks it up).
 */
export function useEngineChatMode(): 'engine' | 'legacy' {
  const enabled = useFeature(ENGINE_CHAT_FLAG);
  const [mode] = useState<'engine' | 'legacy'>(enabled ? 'engine' : 'legacy');
  return mode;
}

/** Feature flag that selects Deep Work on the engine (resolved server-side per tenant). */
export const ENGINE_DEEP_WORK_FLAG = 'ENABLE_ENGINE_DEEP_WORK';

/**
 * Deep Work mode for this mount. Captured once so a flag flip never switches an open card;
 * a session created in one mode stays in it (reload picks up the change).
 */
export function useEngineDeepWorkMode(): 'engine' | 'legacy' {
  const enabled = useFeature(ENGINE_DEEP_WORK_FLAG);
  const [mode] = useState<'engine' | 'legacy'>(enabled ? 'engine' : 'legacy');
  return mode;
}
