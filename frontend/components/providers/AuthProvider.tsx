'use client';

import { useEffect, useState, ReactNode } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { useAuthStore } from '@/lib/stores/auth';
import { useTokenRefresh } from '@/lib/auth/useTokenRefresh';

interface AuthProviderProps {
  children: ReactNode;
}

// Routes that don't require authentication
const PUBLIC_ROUTES = ['/login'];

export function AuthProvider({ children }: AuthProviderProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [hasMounted, setHasMounted] = useState(false);

  // Get auth state and hydrate action
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const isLoading = useAuthStore((state) => state.isLoading);
  const hydrate = useAuthStore((state) => state.hydrate);

  // Global proactive token refresh (24-min interval + visibility-based recovery)
  useTokenRefresh();

  // Hydrate auth state on mount (hydrate is a stable Zustand action)
  useEffect(() => {
    hydrate();
    setHasMounted(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Handle route protection
  useEffect(() => {
    // Wait for client-side mount and hydration
    if (!hasMounted || isLoading) return;

    const isPublicRoute = PUBLIC_ROUTES.some(route => pathname?.startsWith(route));

    if (!isAuthenticated && !isPublicRoute) {
      // Redirect to login if not authenticated
      router.replace('/login');
    } else if (isAuthenticated && pathname === '/login') {
      // Redirect to dashboard if already authenticated
      router.replace('/');
    }
  }, [isAuthenticated, isLoading, pathname, router, hasMounted]);

  // Show loading state while hydrating
  if (!hasMounted || isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="animate-pulse text-muted-foreground">Loading...</div>
      </div>
    );
  }

  return <>{children}</>;
}
