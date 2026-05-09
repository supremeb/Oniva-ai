'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import {
  CircleNotch,
  CheckCircle,
  XCircle,
  Clock,
  Wrench,
  Database,
  Globe,
  Code,
  File,
  MagnifyingGlass,
  Lightning,
} from '@phosphor-icons/react';

export interface ToolCallHeaderProps {
  /** Tool name */
  toolName: string;
  /** Execution status */
  status: 'pending' | 'running' | 'success' | 'error';
  /** Execution duration in milliseconds */
  durationMs?: number;
  /** Additional CSS classes */
  className?: string;
}

// Map tool names to icons
const toolIcons: Record<string, typeof Wrench> = {
  // Database tools
  database: Database,
  query: Database,
  sql: Database,
  // Web tools
  fetch: Globe,
  http: Globe,
  api: Globe,
  browse: Globe,
  // Code tools
  code: Code,
  execute: Code,
  python: Code,
  javascript: Code,
  // File tools
  file: File,
  read: File,
  write: File,
  // Search tools
  search: MagnifyingGlass,
  find: MagnifyingGlass,
  // Default
  default: Wrench,
};

// Get icon based on tool name
function getToolIcon(toolName: string): typeof Wrench {
  const lowerName = toolName.toLowerCase();
  for (const [key, icon] of Object.entries(toolIcons)) {
    if (lowerName.includes(key)) {
      return icon;
    }
  }
  return toolIcons.default;
}

const statusConfig = {
  pending: {
    icon: Clock,
    color: 'text-muted-foreground',
    bgColor: 'bg-muted',
    animate: false,
  },
  running: {
    icon: CircleNotch,
    color: 'text-primary',
    bgColor: 'bg-primary/10',
    animate: true,
  },
  success: {
    icon: CheckCircle,
    color: 'text-green-500',
    bgColor: 'bg-green-500/10',
    animate: false,
  },
  error: {
    icon: XCircle,
    color: 'text-destructive',
    bgColor: 'bg-destructive/10',
    animate: false,
  },
};

/**
 * ToolCallHeader - Header for tool call visualization
 *
 * Displays:
 * - Tool icon based on tool name
 * - Tool name
 * - Status indicator with animation
 * - Duration (if available)
 */
export const ToolCallHeader = memo(function ToolCallHeader({
  toolName,
  status,
  durationMs,
  className,
}: ToolCallHeaderProps) {
  const config = statusConfig[status];
  const StatusIcon = config.icon;
  const ToolIcon = getToolIcon(toolName);

  // Format duration
  const formatDuration = (ms: number): string => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  return (
    <div className={cn('flex items-center gap-2', className)}>
      {/* Tool icon */}
      <div
        className={cn(
          'flex items-center justify-center w-6 h-6 rounded-md',
          config.bgColor
        )}
      >
        <ToolIcon
          className={cn('h-3.5 w-3.5', config.color)}
          weight={status === 'running' ? 'duotone' : 'regular'}
        />
      </div>

      {/* Tool name */}
      <span className="text-sm font-medium text-foreground">
        {toolName}
      </span>

      {/* Status indicator */}
      <StatusIcon
        className={cn(
          'h-4 w-4 ml-auto',
          config.color,
          config.animate && 'animate-spin'
        )}
        weight={status === 'success' ? 'fill' : 'regular'}
      />

      {/* Duration */}
      {durationMs !== undefined && status !== 'running' && (
        <span className="text-xs text-muted-foreground">
          {formatDuration(durationMs)}
        </span>
      )}
    </div>
  );
});

/**
 * ToolCallStatusBadge - Standalone status badge
 */
export interface ToolCallStatusBadgeProps {
  status: 'pending' | 'running' | 'success' | 'error';
  className?: string;
}

export const ToolCallStatusBadge = memo(function ToolCallStatusBadge({
  status,
  className,
}: ToolCallStatusBadgeProps) {
  const config = statusConfig[status];
  const StatusIcon = config.icon;

  const labels = {
    pending: 'Pending',
    running: 'Running',
    success: 'Complete',
    error: 'Failed',
  };

  return (
    <div
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium',
        config.bgColor,
        config.color,
        className
      )}
    >
      <StatusIcon
        className={cn('h-3 w-3', config.animate && 'animate-spin')}
        weight={status === 'success' ? 'fill' : 'regular'}
      />
      <span>{labels[status]}</span>
    </div>
  );
});

export type { ToolCallHeaderProps as ToolCallHeaderComponentProps };
