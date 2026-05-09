'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Avatar, AvatarImage, AvatarFallback } from '@/components/ui/avatar';
import { ArrowSquareOut, FileText } from '@phosphor-icons/react';
import { getFaviconUrl, isValidSourceUrl } from '@/lib/utils/favicon';
import { useRouter } from 'next/navigation';
import type { SourceItem } from '@/lib/models';

interface SourcesPillProps {
  sources: SourceItem[];
  className?: string;
}

const MAX_VISIBLE_FAVICONS = 4;

function isDocumentSource(source: SourceItem): boolean {
  return source.sourceType === 'document' || (!!source.url && source.url.startsWith('/knowledge-bases/'));
}

/**
 * Extract the knowledge base ID from a KB document URL.
 * Expected format: /knowledge-bases/{kbId}/documents/{docId}
 */
function getKbIdFromUrl(url: string): string | null {
  const match = url.match(/^\/knowledge-bases\/([^/]+)/);
  return match ? match[1] : null;
}

/**
 * SourcesPill - Compact citation display with favicon stack and hover dropdown.
 *
 * Collapsed: [favicon stack + "N sources"] pill
 * Hover: Popover dropdown with favicon + title + domain per source
 */
export const SourcesPill = memo(function SourcesPill({
  sources,
  className,
}: SourcesPillProps) {
  const router = useRouter();
  const validSources = sources.filter((s) => isValidSourceUrl(s.url) || isDocumentSource(s));

  if (validSources.length === 0) return null;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          className={cn(
            'inline-flex items-center gap-2 rounded-full px-3 py-1.5',
            'bg-white dark:bg-neutral-900',
            'border border-neutral-200/80 dark:border-neutral-800',
            'text-xs text-neutral-600 dark:text-neutral-300',
            'hover:bg-neutral-50 dark:hover:bg-neutral-800/80',
            'transition-colors cursor-pointer',
            className
          )}
        >
          {/* Stacked favicons / icons */}
          <span className="flex items-center">
            {validSources.slice(0, MAX_VISIBLE_FAVICONS).map((source, i) => (
              <Avatar
                key={source.url}
                className={cn(
                  'h-5 w-5 border border-background',
                  i > 0 && '-ml-2'
                )}
              >
                {isDocumentSource(source) ? (
                  <AvatarFallback className="bg-violet-100 dark:bg-violet-900/40">
                    <FileText className="h-3 w-3 text-violet-600 dark:text-violet-400" weight="fill" />
                  </AvatarFallback>
                ) : (
                  <>
                    <AvatarImage
                      src={getFaviconUrl(source.url)}
                      alt=""
                    />
                    <AvatarFallback className="text-[8px] font-medium">
                      {getDomainInitial(source.url)}
                    </AvatarFallback>
                  </>
                )}
              </Avatar>
            ))}
            {validSources.length > MAX_VISIBLE_FAVICONS && (
              <span className="-ml-2 flex h-5 w-5 items-center justify-center rounded-full border border-background bg-muted text-[9px] font-medium">
                +{validSources.length - MAX_VISIBLE_FAVICONS}
              </span>
            )}
          </span>

          <span>{validSources.length} {validSources.length === 1 ? 'source' : 'sources'}</span>
        </button>
      </PopoverTrigger>

      <PopoverContent
        className="w-72 max-h-[320px] overflow-y-auto p-1.5"
        align="start"
        sideOffset={6}
      >
        <div className="space-y-0.5">
          {validSources.map((source) => {
            const isDoc = isDocumentSource(source);
            return (
              <button
                key={source.url}
                className={cn(
                  'flex items-center gap-2.5 w-full rounded-lg px-2.5 py-2 text-left',
                  'hover:bg-neutral-50 dark:hover:bg-neutral-800/80 transition-colors cursor-pointer'
                )}
                onClick={() => {
                  if (isDoc) {
                    const kbId = getKbIdFromUrl(source.url);
                    if (kbId) {
                      router.push(`/knowledge/${kbId}`);
                    }
                  } else {
                    window.open(source.url, '_blank', 'noopener,noreferrer');
                  }
                }}
              >
                <Avatar className="h-5 w-5 shrink-0">
                  {isDoc ? (
                    <AvatarFallback className="bg-violet-100 dark:bg-violet-900/40">
                      <FileText className="h-3 w-3 text-violet-600 dark:text-violet-400" weight="fill" />
                    </AvatarFallback>
                  ) : (
                    <>
                      <AvatarImage
                        src={getFaviconUrl(source.url)}
                        alt=""
                      />
                      <AvatarFallback className="text-[8px] font-medium">
                        {getDomainInitial(source.url)}
                      </AvatarFallback>
                    </>
                  )}
                </Avatar>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium text-neutral-700 dark:text-neutral-200 truncate">{source.title}</div>
                  <div className="text-[11px] text-neutral-400 dark:text-neutral-500 truncate">
                    {isDoc ? 'Knowledge Base' : getDomain(source.url)}
                  </div>
                </div>
                {isDoc ? (
                  <ArrowSquareOut className="h-3 w-3 text-neutral-400 shrink-0" />
                ) : (
                  <ArrowSquareOut className="h-3 w-3 text-neutral-400 shrink-0" />
                )}
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
});

function getDomain(url: string): string {
  try {
    return new URL(url).hostname.replace('www.', '');
  } catch {
    return url;
  }
}

function getDomainInitial(url: string): string {
  const domain = getDomain(url);
  return domain.charAt(0).toUpperCase();
}
