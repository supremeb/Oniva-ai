'use client';

import { useMemo, useRef } from 'react';
import { useDeepWorkStore, selectIsDeepWorkActive } from '@/lib/stores/deep-work';
import { useShallow } from 'zustand/react/shallow';

/**
 * Compact progress bar that sits above the chat input area during deep work execution.
 * Shows: [pulse dot] Deep Work — {phase} [progress bar] completed/total
 */
export function DeepWorkProgressBar() {
  const { status, enginePhase, todos } = useDeepWorkStore(
    useShallow((s) => ({
      status: s.status,
      enginePhase: s.enginePhase,
      todos: s.todos,
    })),
  );
  const isActive = useDeepWorkStore(selectIsDeepWorkActive);

  const completedTodos = useMemo(
    () => todos.filter((t) => t.status === 'completed').length,
    [todos],
  );

  const progressRef = useRef(0);
  const progress = useMemo(() => {
    if (todos.length === 0) return 0;
    let raw: number;
    if (status === 'completed') {
      raw = 100;
    } else if (enginePhase === 'synthesizing') {
      raw = 92;
    } else if (enginePhase === 'reflecting') {
      raw = 87;
    } else if (enginePhase === 'generating_output') {
      raw = 95;
    } else {
      raw = Math.max(Math.round((completedTodos / todos.length) * 85), 3);
    }
    progressRef.current = Math.max(progressRef.current, raw);
    return progressRef.current;
  }, [todos.length, completedTodos, enginePhase, status]);

  // Only show during active execution
  const isExecuting = isActive && (status === 'executing' || status === 'paused');
  if (!isExecuting) return null;

  const phase =
    enginePhase === 'generating_output'
      ? 'generating'
      : enginePhase === 'synthesizing'
      ? 'writing report'
      : enginePhase === 'reflecting'
      ? 'evaluating'
      : 'researching';

  return (
    <div className="px-6 pb-2">
      <div className="max-w-[680px] mx-auto flex items-center gap-3 px-4 py-2.5 rounded-xl border border-neutral-200 bg-white shadow-sm">
        <div className="h-1.5 w-1.5 rounded-full bg-neutral-900 animate-pulse flex-shrink-0" />
        <span className="text-sm text-neutral-600 truncate">
          Deep Work — {phase}
        </span>
        <div className="flex-1 h-1.5 bg-neutral-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-neutral-900 rounded-full transition-all duration-700 ease-out"
            style={{ width: `${Math.max(progress, 3)}%` }}
          />
        </div>
        <span className="text-xs text-neutral-400 tabular-nums flex-shrink-0">
          {completedTodos}/{todos.length}
        </span>
      </div>
    </div>
  );
}
