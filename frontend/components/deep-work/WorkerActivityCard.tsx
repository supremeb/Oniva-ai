'use client';

import { memo, useState, useCallback, type ReactNode } from 'react';
import {
  CheckCircle,
  CircleNotch,
  CaretRight,
  CaretDown,
  WarningCircle,
  MagnifyingGlass,
  Globe,
  X,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import type { EngineWorkerState } from '@/lib/types/deep-work';

interface WorkerActivityCardProps {
  worker: EngineWorkerState;
  /** Extra expanded content (the engine card's read-only transcript). Called only while expanded. */
  renderDetail?: () => ReactNode;
}

const STATUS_TEXT: Record<EngineWorkerState['status'], string> = {
  idle: 'Queued',
  researching: 'Researching',
  complete: 'Done',
  error: 'Failed',
};

export const WorkerActivityCard = memo(function WorkerActivityCard({
  worker,
  renderDetail,
}: WorkerActivityCardProps) {
  const [expanded, setExpanded] = useState(false);
  const toggleExpand = useCallback(() => setExpanded((v) => !v), []);

  const isActive = worker.status === 'researching';
  const isComplete = worker.status === 'complete';
  const isError = worker.status === 'error';

  // Latest action text for shimmer display
  const latestAction = worker.recentActions?.length
    ? worker.recentActions[worker.recentActions.length - 1]
    : worker.activity || null;

  // Summary text for completed workers
  const summaryText = isComplete && worker.completionSummary
    ? `${worker.completionSummary.findings.length} finding${worker.completionSummary.findings.length !== 1 ? 's' : ''}${worker.completionSummary.sourceCount > 0 ? ` · ${worker.completionSummary.sourceCount} source${worker.completionSummary.sourceCount !== 1 ? 's' : ''}` : ''}${worker.completionSummary.duration > 0 ? ` · ${Math.round(worker.completionSummary.duration)}s` : ''}`
    : isComplete
    ? `${worker.learning_count} finding${worker.learning_count !== 1 ? 's' : ''}`
    : null;

  return (
    <div
      data-testid="worker-row"
      data-status={worker.status}
      className={cn(
        'rounded-lg border transition-colors',
        isError
          ? 'border-red-200 bg-red-50/50'
          : isActive
          ? 'border-blue-100 bg-blue-50/30'
          : 'border-neutral-100 bg-neutral-50/50',
      )}
    >
      {/* ── Header row (always visible, clickable) ── */}
      <button
        onClick={toggleExpand}
        aria-expanded={expanded}
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
      >
        {/* Caret */}
        {expanded ? (
          <CaretDown className="h-3 w-3 text-neutral-400 flex-shrink-0" />
        ) : (
          <CaretRight className="h-3 w-3 text-neutral-400 flex-shrink-0" />
        )}

        {/* Status icon */}
        {isError ? (
          <WarningCircle className="h-3.5 w-3.5 text-red-500 flex-shrink-0" weight="fill" />
        ) : isComplete ? (
          <CheckCircle className="h-3.5 w-3.5 text-neutral-500 flex-shrink-0" weight="fill" />
        ) : isActive ? (
          <CircleNotch className="h-3.5 w-3.5 text-blue-500 flex-shrink-0 animate-spin motion-reduce:animate-none" />
        ) : (
          <MagnifyingGlass className="h-3.5 w-3.5 text-neutral-400 flex-shrink-0" />
        )}

        <span className="sr-only">{STATUS_TEXT[worker.status]}: </span>

        {/* Sub-query text */}
        <span
          className={cn(
            'text-xs truncate min-w-0',
            isError ? 'font-medium text-red-700' : 'font-medium text-neutral-800',
          )}
        >
          {worker.sub_query}
        </span>

        {/* Right side: shimmer action (active) or summary (complete) or error */}
        <span className="ml-auto flex-shrink-0 max-w-[45%] truncate text-right">
          {isActive && latestAction ? (
            <span className="text-xs text-blue-500/80 animate-pulse motion-reduce:animate-none">
              {latestAction}
            </span>
          ) : isError && worker.error ? (
            <span className="text-xs text-red-500 truncate">
              {worker.error}
            </span>
          ) : summaryText ? (
            <span className="text-xs text-neutral-400">
              {summaryText}
            </span>
          ) : null}
        </span>
      </button>

      {/* ── Expandable body ── */}
      {expanded && (
        <div className="px-3 pb-2.5 pt-0.5 ml-[22px] border-t border-neutral-100/80">
          {/* Recent actions COT list */}
          {worker.recentActions && worker.recentActions.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {worker.recentActions.map((action, i) => {
                const isCurrent = isActive && i === worker.recentActions!.length - 1;
                return (
                  <li key={i} className="flex items-center gap-1.5">
                    {isCurrent ? (
                      <div className="h-1 w-1 rounded-full bg-blue-400 animate-pulse motion-reduce:animate-none flex-shrink-0" />
                    ) : (
                      <CheckCircle className="h-2.5 w-2.5 text-neutral-400 flex-shrink-0" weight="fill" />
                    )}
                    <span
                      className={cn(
                        'text-xs truncate',
                        isCurrent ? 'text-neutral-700' : 'text-neutral-400',
                      )}
                    >
                      {action}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}

          {/* Screenshot thumbnail */}
          {worker.lastScreenshot && (
            <ScreenshotThumb
              url={worker.lastScreenshot.url}
              pageUrl={worker.lastScreenshot.pageUrl}
            />
          )}

          {/* Error message */}
          {isError && worker.error && (
            <p className="text-xs text-red-500 mt-1.5 line-clamp-3">{worker.error}</p>
          )}

          {/* Findings list for completed workers */}
          {isComplete && worker.completionSummary?.findings && worker.completionSummary.findings.length > 0 && (
            <ul className="mt-2 space-y-1">
              {worker.completionSummary.findings.map((f, i) => (
                <li key={i} className="text-xs text-neutral-500 leading-relaxed">
                  • {f}
                </li>
              ))}
            </ul>
          )}

          {renderDetail?.()}

          {/* Fallback: no actions, no findings, no error */}
          {!renderDetail && !worker.recentActions?.length && !worker.completionSummary?.findings?.length && !worker.error && (
            <p className="text-xs text-neutral-400 mt-1.5 italic">Waiting...</p>
          )}
        </div>
      )}
    </div>
  );
});

// ── Screenshot thumbnail with lightbox ──────────────────────────

function ScreenshotThumb({ url, pageUrl }: { url: string; pageUrl: string }) {
  const [lightbox, setLightbox] = useState(false);
  const [imgError, setImgError] = useState(false);

  const domain = (() => {
    try {
      return new URL(pageUrl).hostname;
    } catch {
      return pageUrl.slice(0, 40);
    }
  })();

  if (imgError) return null;

  return (
    <>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setLightbox(true); }}
        className="mt-2 group block rounded-md overflow-hidden border border-neutral-200 hover:border-blue-300 transition-colors"
      >
        <img
          src={url}
          alt="Browser screenshot"
          loading="lazy"
          decoding="async"
          onError={() => setImgError(true)}
          className="w-full max-w-[200px] h-auto object-cover"
        />
        {pageUrl && (
          <div className="flex items-center gap-1 px-1.5 py-0.5 bg-neutral-50 border-t border-neutral-100">
            <Globe className="h-2.5 w-2.5 text-neutral-400 flex-shrink-0" />
            <span className="text-[10px] text-neutral-500 truncate">{domain}</span>
          </div>
        )}
      </button>

      {lightbox && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setLightbox(false)}
        >
          <div className="relative max-w-[90vw] max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              onClick={() => setLightbox(false)}
              className="absolute -top-3 -right-3 rounded-full bg-white p-1 shadow-md hover:bg-neutral-100 transition-colors z-10"
            >
              <X className="h-4 w-4 text-neutral-600" />
            </button>
            <img
              src={url}
              alt="Browser screenshot (full)"
              className="max-w-[90vw] max-h-[85vh] object-contain rounded-lg shadow-2xl"
            />
            {pageUrl && (
              <div className="mt-2 text-center">
                <span className="text-xs text-white/80 bg-black/40 px-2 py-1 rounded">
                  {pageUrl}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
