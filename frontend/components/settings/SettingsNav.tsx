'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/lib/stores/auth';
import {
  Robot,
  Key,
  Buildings,
  Users,
  Plugs,
  Database,
} from '@phosphor-icons/react';

interface NavItem {
  name: string;
  href: string;
  icon: React.ElementType;
}

interface NavSection {
  label: string;
  items: NavItem[];
  requiresAdmin?: boolean;
}

const sections: NavSection[] = [
  {
    label: 'GENERAL',
    items: [
      { name: 'LLM Providers', href: '/settings/llm-providers', icon: Robot },
      { name: 'API Keys', href: '/settings/api-keys', icon: Key },
    ],
  },
  {
    label: 'ORGANIZATION',
    requiresAdmin: true,
    items: [
      { name: 'Overview', href: '/settings/organization/overview', icon: Buildings },
      { name: 'Users', href: '/settings/organization/users', icon: Users },
      { name: 'Context', href: '/settings/organization/context', icon: Buildings },
    ],
  },
  {
    label: 'INTEGRATIONS',
    items: [
      { name: 'Connectors', href: '/settings/integrations/connectors', icon: Plugs },
    ],
  },
  {
    label: 'SYSTEM',
    items: [
      { name: 'Cache Controls', href: '/settings/cache-controls', icon: Database },
    ],
  },
];

export function SettingsNav() {
  const pathname = usePathname();
  const user = useAuthStore((state) => state.user);
  const isAdmin = user?.role === 'owner' || user?.role === 'admin';

  return (
    <nav className="w-[200px] shrink-0 space-y-6 py-6 px-4 border-r border-border">
      {sections
        .filter((section) => !section.requiresAdmin || isAdmin)
        .map((section) => (
          <div key={section.label}>
            <p className="text-[11px] font-medium text-muted-foreground tracking-wider mb-2 px-3">
              {section.label}
            </p>
            <div className="space-y-0.5">
              {section.items.map((item) => {
                const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      'flex items-center gap-2.5 px-3 py-1.5 rounded-md text-sm transition-colors',
                      isActive
                        ? 'bg-muted text-foreground font-medium'
                        : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'
                    )}
                  >
                    <item.icon className="h-4 w-4 shrink-0" weight={isActive ? 'fill' : 'regular'} />
                    {item.name}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
    </nav>
  );
}
