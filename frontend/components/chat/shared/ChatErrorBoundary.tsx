'use client';

import React, { Component, ErrorInfo, ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { WarningCircle, ArrowCounterClockwise } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

export interface ChatErrorBoundaryProps {
  children: ReactNode;
  /** Fallback UI when error occurs */
  fallback?: ReactNode;
  /** Callback when error is caught */
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
  /** Callback when retry is clicked */
  onRetry?: () => void;
  /** Additional CSS classes */
  className?: string;
}

interface ChatErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ChatErrorBoundary extends Component<
  ChatErrorBoundaryProps,
  ChatErrorBoundaryState
> {
  constructor(props: ChatErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ChatErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('ChatErrorBoundary caught an error:', error, errorInfo);
    this.props.onError?.(error, errorInfo);
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null });
    this.props.onRetry?.();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div
          className={cn(
            'flex flex-col items-center justify-center p-8 text-center',
            this.props.className
          )}
        >
          <div className="w-16 h-16 rounded-full bg-destructive/10 flex items-center justify-center mb-4">
            <WarningCircle
              className="h-8 w-8 text-destructive"
              weight="duotone"
            />
          </div>
          <h3 className="text-lg font-medium mb-2">Something went wrong</h3>
          <p className="text-sm text-muted-foreground max-w-sm mb-4">
            {this.state.error?.message ||
              'An unexpected error occurred in the chat interface.'}
          </p>
          <Button onClick={this.handleRetry} variant="outline" className="gap-2">
            <ArrowCounterClockwise className="h-4 w-4" />
            Try again
          </Button>
        </div>
      );
    }

    return this.props.children;
  }
}

/**
 * Inline error display for non-fatal errors (e.g., failed message send)
 */
export interface ChatErrorProps {
  message: string;
  onRetry?: () => void;
  onDismiss?: () => void;
  className?: string;
}

export function ChatError({
  message,
  onRetry,
  onDismiss,
  className,
}: ChatErrorProps) {
  return (
    <div
      className={cn(
        'flex items-center gap-3 p-3 rounded-lg bg-destructive/10 border border-destructive/20',
        className
      )}
    >
      <WarningCircle className="h-5 w-5 text-destructive shrink-0" />
      <span className="text-sm text-destructive flex-1">{message}</span>
      <div className="flex gap-2">
        {onRetry && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onRetry}
            className="h-7 px-2 text-xs"
          >
            Retry
          </Button>
        )}
        {onDismiss && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onDismiss}
            className="h-7 px-2 text-xs"
          >
            Dismiss
          </Button>
        )}
      </div>
    </div>
  );
}
