'use client';

import { memo, useState, useEffect, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Brain, FloppyDisk, CircleNotch, Tag } from '@phosphor-icons/react';
import { getMemory, updateMemory } from '@/lib/api/memory';
import type { EpisodicMemory, VisibilityType } from '@/lib/api/memory';

export interface MemoryEditModalProps {
  /** Memory ID to edit */
  memoryId: string;
  /** Whether dialog is open */
  open: boolean;
  /** Callback to control open state */
  onOpenChange: (open: boolean) => void;
}

/**
 * Extract a section value from the combined context_decision string.
 */
function extractSection(combined: string, section: string): string {
  const pattern = new RegExp(`${section}:\\s*([\\s\\S]*?)(?=\\n\\n(?:Context|Decision|Rationale):|$)`);
  const match = combined.match(pattern);
  return match ? match[1].trim() : '';
}

const inputClasses =
  'w-full rounded-lg border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 px-3 py-2 text-sm text-neutral-700 dark:text-neutral-200 placeholder:text-neutral-400 dark:placeholder:text-neutral-500 focus:outline-none focus:ring-2 focus:ring-neutral-300 dark:focus:ring-neutral-600 transition-shadow';

/**
 * MemoryEditModal - Dialog for viewing and editing a memory entry.
 */
export const MemoryEditModal = memo(function MemoryEditModal({
  memoryId,
  open,
  onOpenChange,
}: MemoryEditModalProps) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [context, setContext] = useState('');
  const [decision, setDecision] = useState('');
  const [rationale, setRationale] = useState('');
  const [entitiesText, setEntitiesText] = useState('');
  const [tagsText, setTagsText] = useState('');
  const [visibility, setVisibility] = useState<VisibilityType>('private');

  // Fetch memory when modal opens
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);

    getMemory(memoryId)
      .then((memory: EpisodicMemory) => {
        // Parse sections from context_decision
        const combined = memory.context_decision || '';
        setContext(extractSection(combined, 'Context'));
        setDecision(extractSection(combined, 'Decision'));
        setRationale(memory.rationale || extractSection(combined, 'Rationale'));
        setEntitiesText(memory.entities?.join(', ') || '');
        setTagsText(memory.tags?.join(', ') || '');
        setVisibility(memory.visibility || 'private');
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load memory');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open, memoryId]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);

    try {
      const entities = entitiesText
        .split(',')
        .map((e) => e.trim())
        .filter(Boolean);
      const tags = tagsText
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);

      await updateMemory(memoryId, {
        context: context || undefined,
        decision: decision || undefined,
        rationale: rationale || undefined,
        entities,
        tags,
        visibility,
      });

      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save changes');
    } finally {
      setSaving(false);
    }
  }, [memoryId, context, decision, rationale, entitiesText, tagsText, visibility, onOpenChange]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto gap-5">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Brain className="h-4 w-4 text-neutral-500" weight="duotone" />
            Edit Memory
          </DialogTitle>
          <DialogDescription className="text-xs">
            Edit the saved memory content. Changes will be reflected across all views.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <CircleNotch className="h-5 w-5 animate-spin text-neutral-400" />
          </div>
        ) : (
          <div className="space-y-4">
            {/* Context */}
            <fieldset className="space-y-1.5">
              <label className="text-xs font-medium text-neutral-500 dark:text-neutral-400" htmlFor="memory-context">
                Context
              </label>
              <textarea
                id="memory-context"
                className={`${inputClasses} resize-y min-h-[72px]`}
                placeholder="What was asked or what situation was this about?"
                value={context}
                onChange={(e) => setContext(e.target.value)}
                rows={3}
              />
            </fieldset>

            {/* Decision */}
            <fieldset className="space-y-1.5">
              <label className="text-xs font-medium text-neutral-500 dark:text-neutral-400" htmlFor="memory-decision">
                Decision
              </label>
              <textarea
                id="memory-decision"
                className={`${inputClasses} resize-y min-h-[72px]`}
                placeholder="What decision or recommendation was made?"
                value={decision}
                onChange={(e) => setDecision(e.target.value)}
                rows={3}
              />
            </fieldset>

            {/* Rationale */}
            <fieldset className="space-y-1.5">
              <label className="text-xs font-medium text-neutral-500 dark:text-neutral-400" htmlFor="memory-rationale">
                Rationale
              </label>
              <textarea
                id="memory-rationale"
                className={`${inputClasses} resize-y min-h-[56px]`}
                placeholder="Reasoning behind the decision"
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                rows={2}
              />
            </fieldset>

            {/* Entities */}
            <fieldset className="space-y-1.5">
              <label className="text-xs font-medium text-neutral-500 dark:text-neutral-400" htmlFor="memory-entities">
                Entities
                <span className="font-normal text-neutral-400 dark:text-neutral-500 ml-1.5">comma-separated</span>
              </label>
              <input
                id="memory-entities"
                type="text"
                className={inputClasses}
                placeholder="AAPL, technology, growth"
                value={entitiesText}
                onChange={(e) => setEntitiesText(e.target.value)}
              />
              {entitiesText && (
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {entitiesText.split(',').map((e) => e.trim()).filter(Boolean).map((entity, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-neutral-100 dark:bg-neutral-800 text-neutral-500 dark:text-neutral-400 text-[10px]"
                    >
                      <Tag className="h-2.5 w-2.5" />
                      {entity}
                    </span>
                  ))}
                </div>
              )}
            </fieldset>

            {/* Tags */}
            <fieldset className="space-y-1.5">
              <label className="text-xs font-medium text-neutral-500 dark:text-neutral-400" htmlFor="memory-tags">
                Tags
                <span className="font-normal text-neutral-400 dark:text-neutral-500 ml-1.5">comma-separated</span>
              </label>
              <input
                id="memory-tags"
                type="text"
                className={inputClasses}
                placeholder="important, follow-up"
                value={tagsText}
                onChange={(e) => setTagsText(e.target.value)}
              />
            </fieldset>

            {/* Visibility */}
            <fieldset className="space-y-1.5">
              <label className="text-xs font-medium text-neutral-500 dark:text-neutral-400" htmlFor="memory-visibility">
                Visibility
              </label>
              <select
                id="memory-visibility"
                className={inputClasses}
                value={visibility}
                onChange={(e) => setVisibility(e.target.value as VisibilityType)}
              >
                <option value="private">Private (only you)</option>
                <option value="shared">Shared (organization-wide)</option>
              </select>
            </fieldset>

            {/* Error */}
            {error && (
              <div className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/20 px-3 py-2 text-xs text-red-600 dark:text-red-400">
                {error}
              </div>
            )}
          </div>
        )}

        <DialogFooter className="gap-2">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="inline-flex items-center justify-center rounded-lg border border-neutral-200/80 dark:border-neutral-800 bg-white dark:bg-neutral-900 px-3.5 py-1.5 text-xs font-medium text-neutral-600 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800/80 transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={loading || saving}
            className="inline-flex items-center gap-1.5 justify-center rounded-lg bg-neutral-900 dark:bg-white px-3.5 py-1.5 text-xs font-medium text-white dark:text-neutral-900 hover:bg-neutral-800 dark:hover:bg-neutral-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? (
              <CircleNotch className="h-3 w-3 animate-spin" />
            ) : (
              <FloppyDisk className="h-3 w-3" weight="bold" />
            )}
            Save
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
});
