'use client';

import { memo } from 'react';
import { Lightning, X } from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';

interface DeepWorkSuggestionBannerProps {
  reasoning: string;
  onAccept: () => void;
  onDismiss: () => void;
}

export const DeepWorkSuggestionBanner = memo(function DeepWorkSuggestionBanner({
  reasoning,
  onAccept,
  onDismiss,
}: DeepWorkSuggestionBannerProps) {
  return (
    <div className="mx-6 mb-3 rounded-lg border border-primary/20 bg-primary/5 p-3">
      <div className="flex items-start gap-3">
        <Lightning className="h-4 w-4 text-primary mt-0.5 shrink-0" weight="fill" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground">
            This query looks like it needs deep research
          </p>
          {reasoning && (
            <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{reasoning}</p>
          )}
          <div className="flex items-center gap-2 mt-2">
            <Button size="sm" variant="default" onClick={onAccept} className="h-7 text-xs">
              Switch to Deep Work
            </Button>
            <Button size="sm" variant="ghost" onClick={onDismiss} className="h-7 text-xs text-muted-foreground">
              Continue as chat
            </Button>
          </div>
        </div>
        <button
          onClick={onDismiss}
          className="text-muted-foreground/60 hover:text-muted-foreground shrink-0"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
});
