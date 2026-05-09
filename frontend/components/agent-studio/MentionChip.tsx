'use client';

import { NodeViewWrapper, type NodeViewProps } from '@tiptap/react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/**
 * Color mapping for mention entity types.
 * Blue = skills, Green = connectors, Purple = agents, Orange = knowledge bases.
 */
const TYPE_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  skill: {
    bg: 'bg-blue-50 dark:bg-blue-950/30',
    text: 'text-blue-700 dark:text-blue-300',
    border: 'border-blue-200 dark:border-blue-800',
  },
  connector: {
    bg: 'bg-green-50 dark:bg-green-950/30',
    text: 'text-green-700 dark:text-green-300',
    border: 'border-green-200 dark:border-green-800',
  },
  agent: {
    bg: 'bg-purple-50 dark:bg-purple-950/30',
    text: 'text-purple-700 dark:text-purple-300',
    border: 'border-purple-200 dark:border-purple-800',
  },
  knowledge_base: {
    bg: 'bg-orange-50 dark:bg-orange-950/30',
    text: 'text-orange-700 dark:text-orange-300',
    border: 'border-orange-200 dark:border-orange-800',
  },
};

const TYPE_LABELS: Record<string, string> = {
  skill: 'Skill',
  connector: 'Connector',
  agent: 'Agent',
  knowledge_base: 'Knowledge Base',
};

/**
 * Renders an @mention as a styled inline chip inside the Tiptap editor.
 * Color-coded by entity type with tooltip showing description.
 */
export function MentionChip({ node }: NodeViewProps) {
  const { entityType, entityId, displayName, description, deleted, enabled } =
    node.attrs as {
      entityType: string;
      entityId: string;
      displayName: string;
      description?: string;
      deleted?: boolean;
      enabled?: boolean;
    };

  const styles = TYPE_STYLES[entityType] || TYPE_STYLES.skill;
  const label = TYPE_LABELS[entityType] || entityType;

  const isDeleted = deleted === true;
  const isDisabled = enabled === false && !isDeleted;

  return (
    <NodeViewWrapper as="span" className="inline">
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className={cn(
                'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-medium border cursor-default align-baseline',
                isDeleted
                  ? 'bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-300 border-red-300 dark:border-red-800 line-through'
                  : isDisabled
                    ? 'bg-yellow-50 dark:bg-yellow-950/30 text-yellow-700 dark:text-yellow-300 border-yellow-300 dark:border-yellow-800'
                    : `${styles.bg} ${styles.text} ${styles.border}`,
              )}
              data-entity-type={entityType}
              data-entity-id={entityId}
            >
              @{displayName}
            </span>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs">
            <div className="space-y-1">
              <div className="font-medium text-xs">{label}: {displayName}</div>
              {description && (
                <div className="text-xs text-muted-foreground">{description}</div>
              )}
              {isDeleted && (
                <div className="text-xs text-red-500">
                  This {label.toLowerCase()} has been deleted
                </div>
              )}
              {isDisabled && (
                <div className="text-xs text-yellow-600">
                  Not enabled — enable in sidebar to use
                </div>
              )}
            </div>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </NodeViewWrapper>
  );
}

/**
 * Standalone chip component for use outside the editor (e.g., in lists).
 */
export function MentionChipStatic({
  entityType,
  displayName,
  deleted,
  enabled,
  className,
}: {
  entityType: string;
  displayName: string;
  deleted?: boolean;
  enabled?: boolean;
  className?: string;
}) {
  const styles = TYPE_STYLES[entityType] || TYPE_STYLES.skill;
  const isDeleted = deleted === true;
  const isDisabled = enabled === false && !isDeleted;

  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-medium border',
        isDeleted
          ? 'bg-red-50 text-red-700 border-red-300 line-through'
          : isDisabled
            ? 'bg-yellow-50 text-yellow-700 border-yellow-300'
            : `${styles.bg} ${styles.text} ${styles.border}`,
        className,
      )}
    >
      @{displayName}
    </span>
  );
}
