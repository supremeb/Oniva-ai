"use client"

import { cn } from '@/lib/utils';

interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
    variant?: 'text' | 'circular' | 'rectangular' | 'rounded';
    width?: string | number;
    height?: string | number;
    animation?: 'pulse' | 'wave' | 'none';
}

export function Skeleton({
    className,
    variant = 'text',
    width,
    height,
    animation = 'pulse',
    ...props
}: SkeletonProps) {
    const variantClasses = {
        text: 'h-4 w-full',
        circular: 'rounded-full',
        rectangular: 'rounded-none',
        rounded: 'rounded-lg',
    };

    const animationClasses = {
        pulse: 'animate-pulse',
        wave: 'animate-shimmer',
        none: '',
    };

    return (
        <div
            className={cn(
                'bg-muted',
                variantClasses[variant],
                animationClasses[animation],
                className
            )}
            style={{
                width: width,
                height: height || (variant === 'circular' ? width : undefined),
            }}
            {...props}
        />
    );
}

// Card skeleton for loading states
export function CardSkeleton() {
    return (
        <div className="border rounded-lg p-6 space-y-4">
            <div className="flex items-start justify-between">
                <Skeleton variant="circular" width={48} height={48} />
                <Skeleton variant="text" width="60px" height="20px" />
            </div>
            <div className="space-y-2">
                <Skeleton variant="text" width="70%" height="24px" />
                <Skeleton variant="text" width="100%" height="16px" />
                <Skeleton variant="text" width="90%" height="16px" />
            </div>
            <div className="flex gap-2">
                <Skeleton variant="rounded" width="80px" height="32px" />
                <Skeleton variant="rounded" width="80px" height="32px" />
            </div>
        </div>
    );
}

// Table skeleton for loading states
export function TableSkeleton({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
    return (
        <div className="space-y-2">
            {/* Header */}
            <div className="flex gap-4 p-3 border-b">
                {Array.from({ length: columns }).map((_, i) => (
                    <Skeleton key={`header-${i}`} variant="text" width={`${100 / columns}%`} height="20px" />
                ))}
            </div>
            {/* Rows */}
            {Array.from({ length: rows }).map((_, rowIndex) => (
                <div key={`row-${rowIndex}`} className="flex gap-4 p-3">
                    {Array.from({ length: columns }).map((_, colIndex) => (
                        <Skeleton
                            key={`cell-${rowIndex}-${colIndex}`}
                            variant="text"
                            width={`${100 / columns}%`}
                            height="16px"
                        />
                    ))}
                </div>
            ))}
        </div>
    );
}

// List skeleton for loading states
export function ListSkeleton({ items = 3 }: { items?: number }) {
    return (
        <div className="space-y-3">
            {Array.from({ length: items }).map((_, i) => (
                <div key={i} className="flex items-center gap-3 p-3">
                    <Skeleton variant="circular" width={40} height={40} />
                    <div className="flex-1 space-y-2">
                        <Skeleton variant="text" width="30%" height="18px" />
                        <Skeleton variant="text" width="80%" height="14px" />
                    </div>
                </div>
            ))}
        </div>
    );
}

// Agent card skeleton
export function AgentCardSkeleton() {
    return (
        <div className="border rounded-lg p-6 space-y-4">
            <div className="flex items-center gap-4">
                <Skeleton variant="circular" width={56} height={56} />
                <div className="flex-1 space-y-2">
                    <Skeleton variant="text" width="60%" height="24px" />
                    <Skeleton variant="text" width="40%" height="16px" />
                </div>
            </div>
            <Skeleton variant="text" width="100%" height="14px" />
            <Skeleton variant="text" width="85%" height="14px" />
            <div className="flex gap-2 pt-2">
                <Skeleton variant="rounded" width="100px" height="36px" />
                <Skeleton variant="rounded" width="100px" height="36px" />
            </div>
        </div>
    );
}

// Workflow card skeleton
export function WorkflowCardSkeleton() {
    return (
        <div className="border rounded-lg p-6 space-y-4">
            <div className="space-y-2">
                <Skeleton variant="text" width="70%" height="24px" />
                <Skeleton variant="text" width="100%" height="16px" />
            </div>
            <div className="flex items-center gap-2">
                <Skeleton variant="circular" width={24} height={24} />
                <Skeleton variant="text" width="100px" height="14px" />
            </div>
            <div className="flex justify-between items-center pt-2">
                <Skeleton variant="text" width="80px" height="14px" />
                <Skeleton variant="rounded" width="60px" height="32px" />
            </div>
        </div>
    );
}