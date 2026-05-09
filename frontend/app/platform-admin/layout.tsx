'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/lib/stores/auth';
import { GearSix, Users, ArrowLeft } from '@phosphor-icons/react';
import Link from 'next/link';

/**
 * Platform Admin layout — separate from the customer (dashboard) layout.
 * No customer sidebar. Admin-specific nav. Gated by is_platform_operator.
 */
export default function PlatformAdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const isLoading = useAuthStore((s) => s.isLoading);
  const hydrate = useAuthStore((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  // Redirect non-operators
  useEffect(() => {
    if (!isLoading && (!user || !user.is_platform_operator)) {
      router.replace('/');
    }
  }, [user, isLoading, router]);

  if (isLoading || !user?.is_platform_operator) {
    return (
      <div className="flex items-center justify-center h-screen text-muted-foreground">
        Loading...
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Admin Header */}
      <header className="h-14 border-b border-border bg-background flex items-center px-6 gap-4">
        <Link href="/" className="text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <GearSix className="h-5 w-5 text-primary" weight="fill" />
        <h1 className="text-sm font-semibold">Platform Admin</h1>
        <nav className="flex items-center gap-4 ml-6">
          <Link
            href="/platform-admin/tenants"
            className="text-sm text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1.5"
          >
            <Users className="h-4 w-4" />
            Tenants
          </Link>
        </nav>
        <div className="ml-auto text-xs text-muted-foreground">
          Operator: {user.email}
        </div>
      </header>

      {/* Content */}
      <main className="p-6">
        {children}
      </main>
    </div>
  );
}
