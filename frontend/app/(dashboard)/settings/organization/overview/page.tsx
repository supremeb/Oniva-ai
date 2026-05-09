'use client';

import { useState } from 'react';
import {
  Users,
  Robot,
  Lightning,
  ThumbsUp,
  CaretUp,
  CaretDown,
  Eye,
  X,
} from '@phosphor-icons/react';
import {
  useOrgOverview,
  useAgentFleet,
  useAgentDetail,
  useRevokePermission,
} from '@/lib/api/admin';
import type { AgentFleetItem, AgentPermission } from '@/lib/api/admin';

// ============================================================================
// Metric Card
// ============================================================================

function MetricCard({
  label,
  value,
  icon: Icon,
  suffix,
}: {
  label: string;
  value: number | string | null;
  icon: React.ElementType;
  suffix?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center gap-3 text-muted-foreground mb-2">
        <Icon size={20} weight="duotone" />
        <span className="text-sm font-medium">{label}</span>
      </div>
      <div className="text-2xl font-bold text-foreground">
        {value !== null && value !== undefined ? (
          <>
            {value}
            {suffix && <span className="text-base font-normal text-muted-foreground ml-1">{suffix}</span>}
          </>
        ) : (
          <span className="text-muted-foreground">--</span>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Agent Fleet Table
// ============================================================================

function AgentFleetTable({
  agents,
  onSelectAgent,
  sortBy,
  sortOrder,
  onSort,
}: {
  agents: AgentFleetItem[];
  onSelectAgent: (id: string) => void;
  sortBy: string;
  sortOrder: string;
  onSort: (col: string) => void;
}) {
  const SortIcon = sortOrder === 'asc' ? CaretUp : CaretDown;

  function SortHeader({ col, children }: { col: string; children: React.ReactNode }) {
    return (
      <th
        className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase cursor-pointer hover:text-foreground select-none"
        onClick={() => onSort(col)}
      >
        <span className="inline-flex items-center gap-1">
          {children}
          {sortBy === col && <SortIcon size={12} />}
        </span>
      </th>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-card">
      <table className="min-w-full divide-y divide-border">
        <thead className="bg-muted/50">
          <tr>
            <SortHeader col="name">Agent</SortHeader>
            <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Status</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Model</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Feedback</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase">Satisfaction</th>
            <SortHeader col="created_at">Created</SortHeader>
            <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {agents.map((agent) => (
            <tr key={agent.id} className="hover:bg-muted/50 transition-colors">
              <td className="px-4 py-3">
                <div className="font-medium text-foreground text-sm">{agent.name}</div>
                {agent.description && (
                  <div className="text-xs text-muted-foreground truncate max-w-[240px]">{agent.description}</div>
                )}
              </td>
              <td className="px-4 py-3">
                <span
                  className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                    agent.is_active
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-muted text-muted-foreground'
                  }`}
                >
                  {agent.is_active ? 'Active' : 'Inactive'}
                </span>
              </td>
              <td className="px-4 py-3 text-sm text-muted-foreground">{agent.model || '--'}</td>
              <td className="px-4 py-3 text-sm text-muted-foreground">
                {agent.feedback_total > 0 ? (
                  <span>{agent.feedback_positive}/{agent.feedback_total}</span>
                ) : (
                  <span className="text-muted-foreground">--</span>
                )}
              </td>
              <td className="px-4 py-3">
                {agent.satisfaction_rate !== null ? (
                  <span
                    className={`text-sm font-medium ${
                      agent.satisfaction_rate >= 80
                        ? 'text-green-600 dark:text-green-400'
                        : agent.satisfaction_rate >= 50
                        ? 'text-yellow-600 dark:text-yellow-400'
                        : 'text-red-600 dark:text-red-400'
                    }`}
                  >
                    {agent.satisfaction_rate}%
                  </span>
                ) : (
                  <span className="text-sm text-muted-foreground">--</span>
                )}
              </td>
              <td className="px-4 py-3 text-sm text-muted-foreground">
                {agent.created_at
                  ? new Date(agent.created_at).toLocaleDateString()
                  : '--'}
              </td>
              <td className="px-4 py-3">
                <button
                  onClick={() => onSelectAgent(agent.id)}
                  className="text-muted-foreground hover:text-foreground p-1 rounded transition-colors"
                  title="View details"
                >
                  <Eye size={18} />
                </button>
              </td>
            </tr>
          ))}
          {agents.length === 0 && (
            <tr>
              <td colSpan={7} className="px-4 py-8 text-center text-muted-foreground">
                No agents found
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================================
// Agent Detail Slide-over
// ============================================================================

function AgentDetailPanel({
  agentId,
  onClose,
}: {
  agentId: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useAgentDetail(agentId);
  const revokeMutation = useRevokePermission();

  if (isLoading) {
    return (
      <div className="fixed inset-y-0 right-0 w-[480px] bg-background border-l border-border shadow-xl z-50 p-6">
        <div className="flex justify-between items-center mb-6">
          <div className="h-6 w-40 bg-muted rounded animate-pulse" />
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X size={20} />
          </button>
        </div>
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 bg-muted rounded animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { agent, feedback_summary, recent_feedback, permissions } = data;
  const agentName = (agent as Record<string, unknown>).name as string;

  return (
    <div className="fixed inset-y-0 right-0 w-[480px] bg-background border-l border-border shadow-xl z-50 overflow-y-auto">
      <div className="p-6">
        {/* Header */}
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-lg font-semibold text-foreground">{agentName}</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground p-1">
            <X size={20} />
          </button>
        </div>

        {/* Feedback Summary */}
        <div className="mb-6">
          <h3 className="text-sm font-medium text-foreground mb-3">Feedback (30 days)</h3>
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg bg-green-50 dark:bg-green-900/20 p-3 text-center">
              <div className="text-xl font-bold text-green-700 dark:text-green-400">{feedback_summary.positive}</div>
              <div className="text-xs text-green-600 dark:text-green-500">Positive</div>
            </div>
            <div className="rounded-lg bg-red-50 dark:bg-red-900/20 p-3 text-center">
              <div className="text-xl font-bold text-red-700 dark:text-red-400">{feedback_summary.negative}</div>
              <div className="text-xs text-red-600 dark:text-red-500">Negative</div>
            </div>
            <div className="rounded-lg bg-muted p-3 text-center">
              <div className="text-xl font-bold text-foreground">
                {feedback_summary.satisfaction_rate !== null
                  ? `${feedback_summary.satisfaction_rate}%`
                  : '--'}
              </div>
              <div className="text-xs text-muted-foreground">Satisfaction</div>
            </div>
          </div>
        </div>

        {/* Permissions */}
        <div className="mb-6">
          <h3 className="text-sm font-medium text-foreground mb-3">Permissions</h3>
          {permissions.length > 0 ? (
            <div className="space-y-2">
              {permissions.map((perm: AgentPermission) => (
                <div
                  key={perm.id}
                  className="flex items-center justify-between rounded-lg border border-border px-3 py-2"
                >
                  <div>
                    <span className="text-sm font-medium text-foreground">
                      {perm.permission_type.replace(/_/g, ' ')}
                    </span>
                    <span className="text-xs text-muted-foreground ml-2">
                      Team: {perm.team_id.slice(0, 8)}...
                    </span>
                  </div>
                  <button
                    onClick={() => revokeMutation.mutate(perm.id)}
                    className="text-red-400 hover:text-red-600 text-xs"
                    disabled={revokeMutation.isPending}
                  >
                    Revoke
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No permissions configured</p>
          )}
        </div>

        {/* Recent Feedback */}
        <div>
          <h3 className="text-sm font-medium text-foreground mb-3">Recent Feedback</h3>
          {recent_feedback.length > 0 ? (
            <div className="space-y-2">
              {recent_feedback.map((fb, idx) => (
                <div key={idx} className="rounded-lg border border-border p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className={`inline-block w-2 h-2 rounded-full ${
                        (fb as Record<string, unknown>).rating === 'positive' ? 'bg-green-500' : 'bg-red-500'
                      }`}
                    />
                    <span className="text-xs text-muted-foreground">
                      {(fb as Record<string, unknown>).created_at
                        ? new Date((fb as Record<string, unknown>).created_at as string).toLocaleDateString()
                        : ''}
                    </span>
                  </div>
                  {typeof (fb as Record<string, unknown>).feedback_text === 'string' && (
                    <p className="text-sm text-foreground">
                      {(fb as Record<string, unknown>).feedback_text as string}
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No feedback yet</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Main Organization Overview Page
// ============================================================================

export default function OrganizationOverviewPage() {
  const { data: overview, isLoading: overviewLoading } = useOrgOverview();
  const [sortBy, setSortBy] = useState('created_at');
  const [sortOrder, setSortOrder] = useState('desc');
  const { data: fleet, isLoading: fleetLoading } = useAgentFleet({
    sort_by: sortBy,
    sort_order: sortOrder,
  });
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(col);
      setSortOrder('desc');
    }
  };

  return (
    <div className="p-6 lg:p-8 max-w-5xl">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-page-title">Organization Overview</h1>
        <p className="text-page-description mt-1">
          Organization metrics and agent management
        </p>
      </div>

      {/* Overview Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        {overviewLoading ? (
          [1, 2, 3, 4].map((i) => (
            <div key={i} className="h-24 bg-card rounded-xl border border-border animate-pulse" />
          ))
        ) : (
          <>
            <MetricCard
              label="Agents"
              value={overview ? `${overview.active_agents}/${overview.total_agents}` : null}
              icon={Robot}
              suffix="active"
            />
            <MetricCard label="Users" value={overview?.total_users ?? null} icon={Users} />
            <MetricCard
              label="Executions (30d)"
              value={overview?.executions_30d ?? null}
              icon={Lightning}
            />
            <MetricCard
              label="Satisfaction"
              value={overview?.satisfaction_rate !== null && overview?.satisfaction_rate !== undefined
                ? Math.round(overview.satisfaction_rate)
                : null}
              icon={ThumbsUp}
              suffix="%"
            />
          </>
        )}
      </div>

      {/* Agent Fleet */}
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-foreground mb-1">Agent Fleet</h2>
        <p className="text-sm text-muted-foreground">
          {fleet ? `${fleet.total} agents` : 'Loading...'}
        </p>
      </div>

      {fleetLoading ? (
        <div className="h-64 bg-card rounded-xl border border-border animate-pulse" />
      ) : (
        <AgentFleetTable
          agents={fleet?.agents ?? []}
          onSelectAgent={setSelectedAgentId}
          sortBy={sortBy}
          sortOrder={sortOrder}
          onSort={handleSort}
        />
      )}

      {/* Detail Slide-over */}
      {selectedAgentId && (
        <>
          <div
            className="fixed inset-0 bg-black/20 z-40"
            onClick={() => setSelectedAgentId(null)}
          />
          <AgentDetailPanel
            agentId={selectedAgentId}
            onClose={() => setSelectedAgentId(null)}
          />
        </>
      )}
    </div>
  );
}
