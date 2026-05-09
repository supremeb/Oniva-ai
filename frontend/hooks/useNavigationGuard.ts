'use client';

import { useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';

interface UseNavigationGuardOptions {
  /**
   * Whether navigation should be blocked
   */
  shouldBlock: boolean;

  /**
   * Message to show in the confirmation dialog
   */
  message?: string;

  /**
   * Callback when user confirms navigation
   */
  onConfirm?: () => void;

  /**
   * Callback when user cancels navigation
   */
  onCancel?: () => void;
}

/**
 * Hook to prevent accidental navigation when there are unsaved changes.
 *
 * Handles:
 * - Browser back/forward buttons
 * - Page refresh/close
 * - In-app navigation via Next.js router
 *
 * @example
 * ```tsx
 * const { confirmNavigation } = useNavigationGuard({
 *   shouldBlock: hasUnsavedChanges,
 *   message: 'You have unsaved changes. Are you sure you want to leave?',
 *   onConfirm: () => saveChanges(),
 * });
 * ```
 */
export function useNavigationGuard({
  shouldBlock,
  message = 'You have unsaved changes. Are you sure you want to leave?',
  onConfirm,
  onCancel,
}: UseNavigationGuardOptions) {
  const router = useRouter();
  const shouldBlockRef = useRef(shouldBlock);

  // Keep ref in sync with prop
  useEffect(() => {
    shouldBlockRef.current = shouldBlock;
  }, [shouldBlock]);

  // Handle browser beforeunload event (refresh, close tab)
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!shouldBlockRef.current) return;

      e.preventDefault();
      // Modern browsers ignore custom messages, but we need to set returnValue
      e.returnValue = message;
      return message;
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [message]);

  // Handle popstate (browser back/forward)
  useEffect(() => {
    const handlePopState = (e: PopStateEvent) => {
      if (!shouldBlockRef.current) return;

      const confirmed = window.confirm(message);
      if (confirmed) {
        onConfirm?.();
      } else {
        onCancel?.();
        // Push state back to prevent navigation
        history.pushState(null, '', window.location.href);
      }
    };

    // Push initial state to enable popstate handling
    if (shouldBlock) {
      history.pushState(null, '', window.location.href);
    }

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [shouldBlock, message, onConfirm, onCancel]);

  /**
   * Programmatically confirm navigation with the user.
   * Returns true if navigation should proceed.
   */
  const confirmNavigation = useCallback((): boolean => {
    if (!shouldBlockRef.current) return true;

    const confirmed = window.confirm(message);
    if (confirmed) {
      onConfirm?.();
      return true;
    }

    onCancel?.();
    return false;
  }, [message, onConfirm, onCancel]);

  /**
   * Navigate to a path with confirmation if needed.
   */
  const navigateWithConfirmation = useCallback(
    (path: string) => {
      if (confirmNavigation()) {
        router.push(path);
      }
    },
    [confirmNavigation, router]
  );

  return {
    /**
     * Shows confirmation dialog and returns whether to proceed
     */
    confirmNavigation,

    /**
     * Navigates to path with confirmation if needed
     */
    navigateWithConfirmation,

    /**
     * Whether navigation is currently being blocked
     */
    isBlocking: shouldBlock,
  };
}

