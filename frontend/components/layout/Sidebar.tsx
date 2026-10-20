'use client';

import { memo, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { useFeatures } from '@/lib/hooks/useFeature';
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from '@/components/ui/tooltip';
import { GenerativeO } from '@/components/ui/GenerativeO';
import {
    SquaresFour,
    TreeStructure,
    ChatCircle,
    GearSix,
    Robot,
    BookOpen,
    Lightbulb,
    ChartBar,
    Shield,
    Brain,
    CurrencyDollar,
    Gauge,
    Bell,
    BookOpenText,
    CalendarBlank,
    Broadcast,
    Tray,
} from '@phosphor-icons/react';
import { useInboxCount } from '@/hooks/useInbox';
import { UserProfileMenu } from '@/components/layout/UserProfileMenu';

// ============ Types ============
interface NavItem {
    name: string;
    href: string;
    icon: React.ElementType;
    featureFlag?: string;  // If set, item is hidden when this feature is disabled
}

// ============ Navigation Config ============
// Grouped by lifecycle: Build → Resources → Operate → Monitor
const navGroups: NavItem[][] = [
    // Build
    [
        { name: 'Dashboard', href: '/', icon: SquaresFour },
        { name: 'Agents', href: '/agents', icon: Robot },
        { name: 'Workflows', href: '/workflows', icon: TreeStructure },
        { name: 'Skills', href: '/skills', icon: Lightbulb },
    ],
    // Resources
    [
        { name: 'Knowledge', href: '/knowledge', icon: BookOpenText },
        { name: 'Memory', href: '/memory', icon: Brain },
    ],
    // Operate
    [
        { name: 'Chat', href: '/chat', icon: ChatCircle },
        { name: 'Needs attention', href: '/inbox', icon: Tray },
        { name: 'Fleet', href: '/fleet', icon: Broadcast, featureFlag: 'ENABLE_FLEET_DASHBOARD' },
        { name: 'Schedules', href: '/schedules', icon: CalendarBlank },
    ],
    // Monitor
    [
        { name: 'Observability', href: '/observability', icon: Gauge },
        { name: 'Analytics', href: '/analytics/cost', icon: CurrencyDollar },
        { name: 'Accuracy', href: '/accuracy', icon: ChartBar, featureFlag: 'ENABLE_ACCURACY' },
        { name: 'Compliance', href: '/compliance', icon: Shield, featureFlag: 'ENABLE_COMPLIANCE' },
    ],
];

const bottomNavigation: NavItem[] = [
    { name: 'Alerts', href: '/observability/alerts', icon: Bell },
    { name: 'Documentation', href: '/docs', icon: BookOpen },
    { name: 'Settings', href: '/settings', icon: GearSix },
];

// ============ Components ============
const NavItem = memo(function NavItem({
    item,
    isActive,
    badge = 0,
}: {
    item: NavItem;
    isActive: boolean;
    badge?: number;
}) {
    return (
        <Tooltip delayDuration={0}>
            <TooltipTrigger asChild>
                <Link
                    href={item.href}
                    className={cn(
                        'relative flex items-center justify-center w-9 h-9 rounded-lg transition-colors duration-100',
                        isActive
                            ? 'bg-primary/10 text-primary'
                            : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    )}
                >
                    <item.icon
                        className="h-[18px] w-[18px]"
                        weight={isActive ? 'fill' : 'regular'}
                    />
                    {badge > 0 && (
                        <span
                            data-testid="nav-badge"
                            className="absolute -right-1 -top-1 min-w-4 rounded-full bg-primary px-1 text-center text-[10px] leading-4 text-primary-foreground"
                        >
                            <span aria-hidden="true">{badge > 99 ? '99+' : badge}</span>
                            <span className="sr-only">
                                {badge} {badge === 1 ? 'item needs' : 'items need'} attention
                            </span>
                        </span>
                    )}
                </Link>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={8}>
                {item.name}
            </TooltipContent>
        </Tooltip>
    );
});

// ============ Main Component ============
function SidebarComponent() {
    const pathname = usePathname();
    const [isNavigating, setIsNavigating] = useState(false);
    const previousPathnameRef = useRef(pathname);
    const features = useFeatures();
    const inboxTotal = useInboxCount();

    // Detect navigation - only animate briefly when path actually changes
    useEffect(() => {
        if (pathname !== previousPathnameRef.current) {
            previousPathnameRef.current = pathname;
            setIsNavigating(true);
            const timer = setTimeout(() => setIsNavigating(false), 200);
            return () => clearTimeout(timer);
        }
    }, [pathname]);

    return (
        <TooltipProvider>
            <aside className="w-[60px] border-r border-border bg-background h-screen flex flex-col shrink-0">
                {/* Logo - GenerativeO: static dark O normally, wave animation during navigation */}
                <div className="h-14 flex items-center justify-center border-b border-border">
                    <Link href="/" className="flex items-center justify-center">
                        <GenerativeO
                            size={28}
                            speed={1}
                            animating={isNavigating}
                        />
                    </Link>
                </div>

                {/* Main Navigation */}
                <nav className="flex-1 py-3 px-2.5 overflow-y-auto">
                    {navGroups.map((group, groupIndex) => {
                        const visibleItems = group.filter((item) => {
                            if (item.featureFlag && !features[item.featureFlag]) return false;
                            return true;
                        });
                        if (visibleItems.length === 0) return null;
                        return (
                            <div key={groupIndex}>
                                {groupIndex > 0 && (
                                    <div className="my-2 mx-1.5 border-t border-border/50" />
                                )}
                                <div className="space-y-1">
                                    {visibleItems.map((item) => {
                                        const isActive = pathname === item.href ||
                                            (item.href !== '/' && pathname.startsWith(item.href));
                                        return (
                                            <NavItem key={item.name} item={item} isActive={isActive} badge={item.href === '/inbox' ? inboxTotal : 0} />
                                        );
                                    })}
                                </div>
                            </div>
                        );
                    })}
                </nav>

                {/* Bottom Navigation */}
                <div className="py-3 px-2.5 space-y-1 border-t border-border">
                    {bottomNavigation.map((item) => {
                        const isActive = pathname === item.href || pathname.startsWith(item.href);
                        return (
                            <NavItem key={item.name} item={item} isActive={isActive} />
                        );
                    })}
                </div>

                {/* User Profile */}
                <div className="py-3 px-2.5 border-t border-border">
                    <UserProfileMenu />
                </div>
            </aside>
        </TooltipProvider>
    );
}

export const Sidebar = memo(SidebarComponent);
