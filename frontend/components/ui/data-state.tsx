'use client';

import { type FC, type ReactNode, memo } from 'react';
import { CircleNotch, WarningCircle, WifiSlash, ArrowClockwise } from '@phosphor-icons/react';
import { Button } from './button';
import { cn } from '@/lib/utils';

/**
 * Unified Data State Component
 * Handles loading, error, empty, and offline states consistently across the app
 */

interface DataStateProps {
  isLoading?: boolean;
  isError?: boolean;
  error?: Error | null;
  isEmpty?: boolean;
  isOffline?: boolean;
  children: ReactNode;
  // Customization
  loadingText?: string;
  errorText?: string;
  emptyText?: string;
  emptyIcon?: ReactNode;
  onRetry?: () => void;
  // Size variants
  size?: 'sm' | 'md' | 'lg';
  // Inline mode for smaller sections
  inline?: boolean;
  className?: string;
}

const sizeClasses = {
  sm: {
    icon: 'h-5 w-5',
    text: 'text-xs',
    padding: 'py-4',
    button: 'h-7 text-xs px-2',
  },
  md: {
    icon: 'h-6 w-6',
    text: 'text-sm',
    padding: 'py-8',
    button: 'h-8 text-sm px-3',
  },
  lg: {
    icon: 'h-10 w-10',
    text: 'text-base',
    padding: 'py-16',
    button: 'h-10 text-base px-4',
  },
};

// Loading State
const LoadingState: FC<{ text?: string; size: 'sm' | 'md' | 'lg'; inline?: boolean }> = memo(
  ({ text = 'Loading...', size, inline }) => {
    const classes = sizeClasses[size];

    if (inline) {
      return (
        <div className="flex items-center gap-2 text-muted-foreground">
          <CircleNotch className={cn(classes.icon, 'animate-spin')} />
          <span className={classes.text}>{text}</span>
        </div>
      );
    }

    return (
      <div className={cn('flex flex-col items-center justify-center', classes.padding)}>
        <div className="relative">
          <div className="absolute inset-0 bg-primary/20 rounded-full blur-xl animate-pulse" />
          <CircleNotch className={cn(classes.icon, 'text-primary animate-spin relative')} weight="bold" />
        </div>
        <span className={cn(classes.text, 'text-muted-foreground mt-3 animate-pulse')}>{text}</span>
      </div>
    );
  }
);
LoadingState.displayName = 'LoadingState';

// Error State
const ErrorState: FC<{
  text?: string;
  size: 'sm' | 'md' | 'lg';
  inline?: boolean;
  onRetry?: () => void;
}> = memo(({ text = 'Something went wrong', size, inline, onRetry }) => {
  const classes = sizeClasses[size];

  if (inline) {
    return (
      <div className="flex items-center gap-2 text-destructive">
        <WarningCircle className={classes.icon} weight="fill" />
        <span className={classes.text}>{text}</span>
        {onRetry && (
          <Button variant="ghost" size="sm" onClick={onRetry} className="ml-2 h-6 px-2">
            <ArrowClockwise className="h-3 w-3" />
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className={cn('flex flex-col items-center justify-center', classes.padding)}>
      <div className="relative">
        <div className="absolute inset-0 bg-destructive/10 rounded-full blur-xl" />
        <WarningCircle className={cn(classes.icon, 'text-destructive relative')} weight="fill" />
      </div>
      <span className={cn(classes.text, 'text-muted-foreground mt-3 text-center max-w-xs')}>{text}</span>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry} className={cn('mt-4 gap-2', classes.button)}>
          <ArrowClockwise className="h-3.5 w-3.5" />
          Try again
        </Button>
      )}
    </div>
  );
});
ErrorState.displayName = 'ErrorState';

// Offline State
const OfflineState: FC<{ size: 'sm' | 'md' | 'lg'; inline?: boolean }> = memo(({ size, inline }) => {
  const classes = sizeClasses[size];

  if (inline) {
    return (
      <div className="flex items-center gap-2 text-amber-500">
        <WifiSlash className={classes.icon} weight="fill" />
        <span className={classes.text}>Offline - showing cached data</span>
      </div>
    );
  }

  return (
    <div className={cn('flex flex-col items-center justify-center', classes.padding)}>
      <div className="relative">
        <div className="absolute inset-0 bg-amber-500/10 rounded-full blur-xl" />
        <WifiSlash className={cn(classes.icon, 'text-amber-500 relative')} weight="fill" />
      </div>
      <span className={cn(classes.text, 'text-muted-foreground mt-3')}>
        You&apos;re offline
      </span>
      <span className={cn('text-xs text-muted-foreground/70 mt-1')}>
        Showing cached data if available
      </span>
    </div>
  );
});
OfflineState.displayName = 'OfflineState';

// Empty State
const EmptyState: FC<{
  text?: string;
  icon?: ReactNode;
  size: 'sm' | 'md' | 'lg';
  inline?: boolean;
}> = memo(({ text = 'No data found', icon, size, inline }) => {
  const classes = sizeClasses[size];

  if (inline) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        {icon}
        <span className={classes.text}>{text}</span>
      </div>
    );
  }

  return (
    <div className={cn('flex flex-col items-center justify-center', classes.padding)}>
      {icon && <div className="text-muted-foreground/30 mb-3">{icon}</div>}
      <span className={cn(classes.text, 'text-muted-foreground')}>{text}</span>
    </div>
  );
});
EmptyState.displayName = 'EmptyState';

/**
 * Main DataState component - wraps content and shows appropriate state
 */
export const DataState: FC<DataStateProps> = ({
  isLoading = false,
  isError = false,
  error,
  isEmpty = false,
  isOffline = false,
  children,
  loadingText,
  errorText,
  emptyText,
  emptyIcon,
  onRetry,
  size = 'md',
  inline = false,
  className,
}) => {
  // Priority: Loading > Error > Offline > Empty > Content
  if (isLoading) {
    return (
      <div className={className}>
        <LoadingState text={loadingText} size={size} inline={inline} />
      </div>
    );
  }

  if (isError) {
    const displayError = errorText || error?.message || 'Failed to load data';
    return (
      <div className={className}>
        <ErrorState text={displayError} size={size} inline={inline} onRetry={onRetry} />
      </div>
    );
  }

  if (isOffline) {
    return (
      <div className={className}>
        <OfflineState size={size} inline={inline} />
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className={className}>
        <EmptyState text={emptyText} icon={emptyIcon} size={size} inline={inline} />
      </div>
    );
  }

  return <>{children}</>;
};

/**
 * Skeleton loader for content placeholders
 */
interface SkeletonGridProps {
  count?: number;
  columns?: 1 | 2 | 3 | 4;
  className?: string;
}

export const SkeletonGrid: FC<SkeletonGridProps> = memo(({ count = 4, columns = 4, className }) => {
  const gridCols = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 sm:grid-cols-2',
    3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4',
  };

  return (
    <div className={cn('grid gap-4', gridCols[columns], className)}>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="h-24 rounded-xl bg-muted/50 animate-pulse"
          style={{ animationDelay: `${i * 100}ms` }}
        />
      ))}
    </div>
  );
});
SkeletonGrid.displayName = 'SkeletonGrid';

/**
 * Card skeleton for individual card placeholders
 */
export const CardSkeleton: FC<{ className?: string }> = memo(({ className }) => (
  <div className={cn('rounded-xl border border-border/40 p-4 space-y-3', className)}>
    <div className="flex items-center gap-3">
      <div className="w-10 h-10 rounded-lg bg-muted/50 animate-pulse" />
      <div className="flex-1 space-y-2">
        <div className="h-4 w-3/4 bg-muted/50 rounded animate-pulse" />
        <div className="h-3 w-1/2 bg-muted/50 rounded animate-pulse" />
      </div>
    </div>
  </div>
));
CardSkeleton.displayName = 'CardSkeleton';
