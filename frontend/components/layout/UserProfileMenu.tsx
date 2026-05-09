'use client';

import { useCallback } from 'react';
import { useAuthStore } from '@/lib/stores/auth';
import { logout } from '@/lib/api/auth';
import { authChannel } from '@/lib/auth/cross-tab';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { SignOut, GearSix } from '@phosphor-icons/react';
import Link from 'next/link';

function getInitials(name: string | undefined, email: string): string {
  if (name) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    return name[0].toUpperCase();
  }
  return email[0].toUpperCase();
}

export function UserProfileMenu() {
  const user = useAuthStore((state) => state.user);
  const clearAuth = useAuthStore((state) => state.clearAuth);

  const handleLogout = useCallback(async () => {
    // Call backend first to revoke refresh token + blacklist access token JTI
    // 3-second timeout — clear client state regardless of backend result
    try {
      await Promise.race([
        logout(),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 3000)),
      ]);
    } catch {
      // Backend unreachable or timed out — proceed with client-side cleanup
    }
    clearAuth();
    authChannel.broadcast('logout');
    window.location.href = '/login';
  }, [clearAuth]);

  if (!user) return null;

  const initials = getInitials(user.name, user.email);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button className="flex items-center justify-center mx-auto focus:outline-none">
          <Avatar className="h-8 w-8 cursor-pointer ring-2 ring-transparent hover:ring-primary/20 transition-all duration-100">
            <AvatarFallback className="text-xs font-medium bg-primary/10 text-primary">
              {initials}
            </AvatarFallback>
          </Avatar>
        </button>
      </PopoverTrigger>
      <PopoverContent side="right" align="end" sideOffset={8} className="w-56 p-0">
        <div className="px-3 py-2.5 border-b border-border">
          <p className="text-sm font-medium truncate">{user.name || user.email}</p>
          <p className="text-xs text-muted-foreground truncate">{user.email}</p>
          <p className="text-[10px] text-muted-foreground capitalize mt-0.5">{user.role}</p>
        </div>
        <div className="py-1">
          <Link
            href="/settings"
            className="flex items-center gap-2 px-3 py-1.5 text-sm text-foreground hover:bg-muted transition-colors"
          >
            <GearSix className="h-4 w-4" />
            Settings
          </Link>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 px-3 py-1.5 text-sm text-destructive hover:bg-destructive/10 transition-colors w-full text-left"
          >
            <SignOut className="h-4 w-4" />
            Sign out
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
