'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Progress } from '@/components/ui/progress';
import {
  ClockCounterClockwise,
  CircleNotch,
  ChartLineUp,
  Play,
  Plus,
  Rocket,
  Warning,
} from '@phosphor-icons/react';
import { cn, formatAccuracy, parseTimestamp } from '@/lib/utils';
import { DeltaIndicator } from '@/components/ui/delta-indicator';
import {
  getAccuracySummary,
  getAccuracyHistory,
  type AccuracySummary,
  type AccuracyHistoryEntry,
} from '@/lib/api/agent-studio';
import { formatVersionNumber } from '@/lib/api/agent-versions';
import { useAgentVersions, useDraftStatus, useCreateAgentVersion } from '@/hooks/useAgentVersions';
import { useQuery } from '@tanstack/react-query';

interface VersionsPanelProps {
  agentId: string;
  refreshTrigger?: number;
  onRunEvaluation?: (versionId: string) => void;
}

export function VersionsPanel({
  agentId,
  refreshTrigger = 0,
  onRunEvaluation,
}: VersionsPanelProps) {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newVersionName, setNewVersionName] = useState('');

  // TanStack Query hooks for backend-first data
  const { data: versionData, isLoading: versionsLoading } = useAgentVersions(agentId);
  const { data: draftStatus } = useDraftStatus(agentId);
  const createVersion = useCreateAgentVersion();

  const versions = versionData?.versions || [];
  const productionVersionNumber = versionData?.production_version_number;

  // Accuracy metrics (non-critical)
  const { data: accuracy } = useQuery({
    queryKey: ['agent-accuracy', agentId],
    queryFn: () => getAccuracySummary(agentId),
    enabled: !!agentId,
    staleTime: 60000,
  });

  const { data: accuracyHistory } = useQuery({
    queryKey: ['agent-accuracy-history', agentId],
    queryFn: () => getAccuracyHistory(agentId, 10),
    enabled: !!agentId,
    staleTime: 60000,
  });

  const handleCreateVersion = async () => {
    if (!newVersionName.trim()) return;
    try {
      await createVersion.mutateAsync({
        agent_id: agentId,
        name: newVersionName.trim(),
      });
      setNewVersionName('');
      setShowCreateForm(false);
    } catch (e) {
      console.error('Failed to create version:', e);
    }
  };

  return (
    <div className="flex flex-col h-full bg-background">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b">
        <div className="flex items-center gap-2">
          <ClockCounterClockwise className="h-5 w-5 text-muted-foreground" />
          <span className="font-medium">Versions</span>
          <Badge variant="secondary" className="text-xs">
            {versions.length}
          </Badge>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs gap-1"
          onClick={() => setShowCreateForm(!showCreateForm)}
        >
          <Plus className="h-3 w-3" />
          Create Version
        </Button>
      </div>

      {/* Draft Change Indicator */}
      {draftStatus?.has_changes && (
        <div className="mx-3 mt-3 p-2.5 rounded-lg border border-amber-500/30 bg-amber-500/5">
          <div className="flex items-center gap-2 text-xs">
            <Warning className="h-3.5 w-3.5 text-amber-500 shrink-0" />
            <span className="text-amber-600 dark:text-amber-400">
              Draft has changes since {draftStatus.latest_version_number ? `v${draftStatus.latest_version_number}` : 'last version'}
            </span>
          </div>
          {draftStatus.changed_fields.length > 0 && (
            <div className="mt-1 ml-5.5 text-xs text-muted-foreground">
              Changed: {draftStatus.changed_fields.slice(0, 3).join(', ')}
              {draftStatus.changed_fields.length > 3 && ` +${draftStatus.changed_fields.length - 3} more`}
            </div>
          )}
        </div>
      )}

      {/* Create Version Form */}
      {showCreateForm && (
        <div className="mx-3 mt-3 p-3 rounded-lg border bg-card space-y-2">
          <Input
            placeholder="Version name (e.g., 'Added OFAC check')"
            value={newVersionName}
            onChange={(e) => setNewVersionName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreateVersion()}
            className="h-8 text-sm"
            autoFocus
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              className="h-7 text-xs flex-1"
              onClick={handleCreateVersion}
              disabled={!newVersionName.trim() || createVersion.isPending}
            >
              {createVersion.isPending ? (
                <CircleNotch className="h-3 w-3 animate-spin mr-1" />
              ) : null}
              Create
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => { setShowCreateForm(false); setNewVersionName(''); }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {versionsLoading ? (
        <div className="flex items-center justify-center h-32">
          <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="p-3 space-y-4">
            {/* Accuracy Summary Card */}
            {accuracy && accuracy.total_tests > 0 && (
              <div className="p-3 rounded-lg border bg-card">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <ChartLineUp className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">Current Accuracy</span>
                  </div>
                  <span className="text-lg font-bold">
                    {formatAccuracy(accuracy.accuracy)}
                  </span>
                </div>
                <Progress
                  value={accuracy.accuracy * 100}
                  className={cn(
                    'h-2',
                    accuracy.accuracy >= 0.8
                      ? '[&>div]:bg-green-500'
                      : accuracy.accuracy >= 0.6
                      ? '[&>div]:bg-amber-500'
                      : '[&>div]:bg-red-500'
                  )}
                />
                <div className="flex justify-between mt-2 text-xs text-muted-foreground">
                  <span>
                    {accuracy.passed_tests}/{accuracy.total_tests} tests passed
                  </span>
                  {accuracy.last_evaluated_at && (
                    <span>
                      {parseTimestamp(accuracy.last_evaluated_at).toLocaleDateString()}
                    </span>
                  )}
                </div>

                {/* Category breakdown */}
                {accuracy.category_breakdown &&
                  Object.keys(accuracy.category_breakdown).length > 0 && (
                    <div className="mt-3 pt-3 border-t space-y-1.5">
                      <span className="text-xs text-muted-foreground">By category:</span>
                      {Object.entries(accuracy.category_breakdown).map(([cat, data]) => (
                        <div key={cat} className="flex items-center justify-between text-xs">
                          <span className="text-muted-foreground">{cat}</span>
                          <span className="font-medium">
                            {formatAccuracy(data.accuracy)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
              </div>
            )}

            {/* Version List */}
            <div className="space-y-2">
              <span className="text-xs font-medium text-muted-foreground">
                Version History
              </span>
              {versions.length === 0 ? (
                <div className="text-center py-6 text-muted-foreground">
                  <ClockCounterClockwise className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">No versions yet</p>
                  <p className="text-xs mt-1">Create a version to snapshot your agent config</p>
                </div>
              ) : (
                versions.map((version) => {
                  const isProduction = version.version_number === productionVersionNumber;
                  const isLegacy = !version.version_number && version.legacy_version;

                  return (
                    <div
                      key={version.id}
                      className={cn(
                        "p-3 rounded-lg border bg-card hover:bg-accent/50 transition-colors",
                        isProduction && "border-green-500/40 bg-green-500/5"
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm font-mono">
                            {version.version_number ? `v${version.version_number}` : version.legacy_version || 'v?'}
                          </span>
                          {isProduction && (
                            <Badge variant="default" className="text-[10px] h-4 px-1.5 bg-green-600 hover:bg-green-600">
                              <Rocket className="h-2.5 w-2.5 mr-0.5" />
                              PRODUCTION
                            </Badge>
                          )}
                          {isLegacy && (
                            <Badge variant="outline" className="text-[10px] h-4 px-1.5 text-muted-foreground">
                              legacy
                            </Badge>
                          )}
                        </div>
                        {version.accuracy_at_deploy !== undefined &&
                          version.accuracy_at_deploy !== null && (
                            <span className="text-xs font-medium">
                              {formatAccuracy(version.accuracy_at_deploy)}
                            </span>
                          )}
                      </div>
                      {version.name && (
                        <div className="mt-1 text-xs text-foreground/80 truncate">
                          {version.name}
                        </div>
                      )}
                      <div className="flex items-center justify-between mt-1 text-xs text-muted-foreground">
                        <span>{parseTimestamp(version.created_at).toLocaleDateString()}</span>
                        {version.notes && (
                          <span className="truncate max-w-[150px]">{version.notes}</span>
                        )}
                      </div>
                      {onRunEvaluation && (
                        <div className="mt-2">
                          <Button
                            variant="outline"
                            size="sm"
                            className="w-full h-7 text-xs gap-1"
                            onClick={() => onRunEvaluation(version.id)}
                          >
                            <Play className="h-3 w-3" />
                            Evaluate
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>

            {/* Accuracy History */}
            {accuracyHistory && accuracyHistory.length > 0 && (
              <div className="space-y-2">
                <span className="text-xs font-medium text-muted-foreground">
                  Accuracy Trend
                </span>
                <div className="space-y-1">
                  {accuracyHistory.slice(0, 5).map((entry: AccuracyHistoryEntry) => (
                    <div
                      key={entry.id}
                      className="flex items-center justify-between text-xs p-2 rounded bg-muted/50"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-muted-foreground">
                          {entry.version ? `v${entry.version}` : 'Draft'}
                        </span>
                        <DeltaIndicator delta={entry.accuracy_delta} size="sm" />
                      </div>
                      <span className="font-medium">{formatAccuracy(entry.accuracy)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
