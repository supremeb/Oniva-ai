'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Brain, PencilSimple, Tag } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { ActionButton } from './ActionButton';
import { MemoryEditModal } from './MemoryEditModal';

export interface MemorySavedCardProps {
  /** Memory ID returned from store_decision */
  memoryId: string;
  /** Snippet preview of the saved content */
  snippet: string;
  /** Agent that saved the memory */
  agentName?: string;
  /** Type of action (analysis, recommendation, etc.) */
  actionType?: string;
  /** Additional CSS classes */
  className?: string;
}

/**
 * MemorySavedCard - Inline card shown when an agent saves to memory.
 *
 * Displayed in chat stream after store_decision completes.
 * Clean neutral design matching the unified pill system.
 */
export const MemorySavedCard = memo(function MemorySavedCard({
  memoryId,
  snippet,
  agentName,
  actionType,
  className,
}: MemorySavedCardProps) {
  const [editOpen, setEditOpen] = useState(false);

  const handleEditClick = useCallback(() => {
    setEditOpen(true);
  }, []);

  return (
    <>
      <FadeSlide>
        <SpringCard
          variant="outline"
          className={cn('overflow-hidden', className)}
        >
          {/* Header */}
          <div className="flex items-start gap-3">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg shrink-0 bg-neutral-100 dark:bg-neutral-800">
              <Brain className="h-4 w-4 text-neutral-500" weight="duotone" />
            </div>

            <div className="flex-1 min-w-0">
              <h4 className="font-medium text-sm text-neutral-700 dark:text-neutral-200">
                Saved to Memory
              </h4>
              <div className="flex items-center gap-2 text-xs text-neutral-400 dark:text-neutral-500 mt-0.5">
                {agentName && <span>{agentName}</span>}
                {agentName && actionType && <span>&middot;</span>}
                {actionType && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-neutral-100 dark:bg-neutral-800 text-[10px] text-neutral-500 dark:text-neutral-400">
                    <Tag className="h-2.5 w-2.5" />
                    {actionType}
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Snippet Preview */}
          {snippet && (
            <div className="mt-3 px-3 py-2.5 bg-neutral-50 dark:bg-neutral-800/50 rounded-lg">
              <p className="text-xs text-neutral-600 dark:text-neutral-300 leading-relaxed line-clamp-3">{snippet}</p>
            </div>
          )}

          {/* Edit Action */}
          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-neutral-200/60 dark:border-neutral-800">
            <ActionButton
              variant="outline"
              size="sm"
              onClick={handleEditClick}
              icon={<PencilSimple className="h-3.5 w-3.5" />}
            >
              Edit
            </ActionButton>
          </div>
        </SpringCard>
      </FadeSlide>

      <MemoryEditModal
        memoryId={memoryId}
        open={editOpen}
        onOpenChange={setEditOpen}
      />
    </>
  );
});
