'use client';

import { memo } from 'react';
import {
  CheckCircle,
  XCircle,
  Clock,
  MagnifyingGlass,
  Sparkle,
} from '@phosphor-icons/react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

export interface WideResearchItemResult {
  item_index: number;
  item: Record<string, unknown>;
  status: string;
  result?: Record<string, unknown>;
  error?: string;
  duration_seconds: number;
}

export interface WideResearchResultsViewerProps {
  executionId: string;
  status: string;
  totalItems: number;
  completed: number;
  failed: number;
  skipped: number;
  items: WideResearchItemResult[];
  synthesis?: string;
  durationSeconds: number;
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle className="h-3 w-3 text-emerald-600" weight="fill" />,
  failed: <XCircle className="h-3 w-3 text-rose-600" weight="fill" />,
  timeout: <Clock className="h-3 w-3 text-orange-600" weight="fill" />,
  skipped: <Clock className="h-3 w-3 text-slate-400" />,
};

const STATUS_COLOR: Record<string, string> = {
  completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  partial: 'bg-amber-50 text-amber-700 border-amber-200',
  failed: 'bg-rose-50 text-rose-700 border-rose-200',
  aborted: 'bg-orange-50 text-orange-700 border-orange-200',
};

function WideResearchResultsViewerComponent({
  executionId,
  status,
  totalItems,
  completed,
  failed,
  skipped,
  items,
  synthesis,
  durationSeconds,
}: WideResearchResultsViewerProps) {
  const statusColor = STATUS_COLOR[status] ?? STATUS_COLOR.completed;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm flex items-center gap-2">
            <MagnifyingGlass className="h-4 w-4" />
            Wide Research
          </CardTitle>
          <Badge variant="outline" className={cn('text-[10px] gap-1', statusColor)}>
            {status}
          </Badge>
        </div>
        <div className="flex gap-3 text-[10px] text-muted-foreground mt-1">
          <span>{totalItems} items</span>
          <span>{completed} completed</span>
          {failed > 0 && <span className="text-rose-600">{failed} failed</span>}
          {skipped > 0 && <span>{skipped} skipped</span>}
          <span>{durationSeconds.toFixed(1)}s</span>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Synthesis Summary */}
        {synthesis && (
          <div className="p-3 bg-violet-50 rounded-lg border border-violet-200">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Sparkle className="h-3 w-3 text-violet-600" weight="fill" />
              <span className="text-[10px] font-medium text-violet-700">Expert Synthesis</span>
            </div>
            <p className="text-[11px] text-violet-900 whitespace-pre-wrap">{synthesis}</p>
          </div>
        )}

        {/* Per-item Results Table */}
        <div className="border rounded-lg overflow-hidden">
          <div className="bg-slate-50 px-3 py-1.5 border-b">
            <p className="text-[10px] font-medium text-slate-600">
              Results ({items.length} items)
            </p>
          </div>
          <div className="max-h-72 overflow-y-auto divide-y">
            {items.map((item) => (
              <div
                key={item.item_index}
                className="px-3 py-2 flex items-start gap-2 text-[11px]"
              >
                <div className="mt-0.5 flex-shrink-0">
                  {STATUS_ICON[item.status] ?? STATUS_ICON.skipped}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-muted-foreground">#{item.item_index + 1}</span>
                    <span className="font-medium truncate">
                      {JSON.stringify(item.item).slice(0, 60)}
                    </span>
                  </div>
                  {item.result && (
                    <pre className="mt-1 text-[10px] text-slate-600 bg-slate-50 p-1.5 rounded overflow-x-auto max-h-20">
                      {JSON.stringify(item.result, null, 2).slice(0, 200)}
                    </pre>
                  )}
                  {item.error && (
                    <p className="mt-1 text-[10px] text-rose-600">{item.error}</p>
                  )}
                </div>
                <span className="text-[10px] text-muted-foreground flex-shrink-0">
                  {item.duration_seconds.toFixed(1)}s
                </span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export const WideResearchResultsViewer = memo(WideResearchResultsViewerComponent);
