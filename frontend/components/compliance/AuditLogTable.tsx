'use client';

import { type FC, useState, useMemo } from 'react';
import { format } from 'date-fns';
import { parseTimestamp } from '@/lib/utils';
import {
  ArrowUp,
  ArrowDown,
  FunnelSimple,
  MagnifyingGlass,
  Download,
  Eye,
} from '@phosphor-icons/react';
import type { ComplianceAuditLog } from '@/lib/api/compliance';
import { SignatureVerificationBadge } from './SignatureVerificationBadge';

interface AuditLogTableProps {
  logs: ComplianceAuditLog[];
  isLoading?: boolean;
  onViewDetails?: (log: ComplianceAuditLog) => void;
  onExport?: () => void;
}

export const AuditLogTable: FC<AuditLogTableProps> = ({
  logs,
  isLoading = false,
  onViewDetails,
  onExport,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState<keyof ComplianceAuditLog>('timestamp');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  const [stateFilter, setStateFilter] = useState<string>('ALL');

  const filteredAndSortedLogs = useMemo(() => {
    let filtered = logs;

    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(
        (log) =>
          log.decision.toLowerCase().includes(term) ||
          log.agent_id.toLowerCase().includes(term) ||
          (log.workflow_id ?? '').toLowerCase().includes(term)
      );
    }

    if (stateFilter !== 'ALL') {
      filtered = filtered.filter((log) => log.workflow_state === stateFilter);
    }

    return [...filtered].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];
      if (aVal === undefined || aVal === null) return 1;
      if (bVal === undefined || bVal === null) return -1;

      if (sortDirection === 'asc') {
        return aVal > bVal ? 1 : -1;
      } else {
        return aVal < bVal ? 1 : -1;
      }
    });
  }, [logs, searchTerm, sortField, sortDirection, stateFilter]);

  const handleSort = (field: keyof ComplianceAuditLog) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  const getWorkflowStateStyle = (state: string) => {
    switch (state) {
      case 'COMPLIANCE_CHECK':
      case 'HUMAN_REVIEW':
        return 'bg-muted/80 text-foreground border-border/60 font-medium';
      case 'COMPLETED':
        return 'bg-muted/60 text-muted-foreground border-border/60';
      default:
        return 'bg-muted/60 text-muted-foreground border-border/60';
    }
  };

  if (isLoading) {
    return (
      <div className="animate-pulse space-y-3">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-16 bg-muted/50 rounded-lg" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filters and Search */}
      <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
        <div className="relative flex-1 max-w-md">
          <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search by decision, agent, or workflow..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="
              w-full pl-10 pr-4 py-2
              bg-muted/50 border border-border
              rounded-lg text-sm text-foreground
              placeholder:text-muted-foreground
              focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/30
              transition-all duration-200
            "
          />
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 px-3 py-1.5 bg-muted/30 rounded-lg border border-border">
            <FunnelSimple className="h-4 w-4 text-muted-foreground" />
            <select
              value={stateFilter}
              onChange={(e) => setStateFilter(e.target.value)}
              className="
                bg-transparent text-sm text-foreground
                focus:outline-none cursor-pointer
              "
            >
              <option value="ALL">All States</option>
              <option value="INITIALIZED">Initialized</option>
              <option value="DATA_GATHERING">Data Gathering</option>
              <option value="ANALYSIS">Analysis</option>
              <option value="COMPLIANCE_CHECK">Compliance Check</option>
              <option value="HUMAN_REVIEW">Human Review</option>
              <option value="REPORT_GENERATION">Report Generation</option>
              <option value="COMPLETED">Completed</option>
            </select>
          </div>

          {onExport && (
            <button
              onClick={onExport}
              className="
                px-3 py-1.5
                bg-muted border border-border
                rounded-lg text-sm text-muted-foreground
                hover:bg-accent hover:text-foreground transition-colors
                flex items-center gap-1.5
              "
            >
              <Download className="h-4 w-4" />
              Export
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-border bg-card">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th
                onClick={() => handleSort('timestamp')}
                className="px-4 py-3 text-left text-table-head cursor-pointer hover:text-foreground transition-colors"
              >
                <div className="flex items-center gap-1">
                  Timestamp
                  {sortField === 'timestamp' && (
                    sortDirection === 'desc' ?
                    <ArrowDown className="h-3 w-3" /> :
                    <ArrowUp className="h-3 w-3" />
                  )}
                </div>
              </th>
              <th className="px-4 py-3 text-left text-table-head">
                Decision
              </th>
              <th className="px-4 py-3 text-left text-table-head">
                Agent
              </th>
              <th
                onClick={() => handleSort('workflow_state')}
                className="px-4 py-3 text-left text-table-head cursor-pointer hover:text-foreground transition-colors"
              >
                <div className="flex items-center gap-1">
                  State
                  {sortField === 'workflow_state' && (
                    sortDirection === 'desc' ?
                    <ArrowDown className="h-3 w-3" /> :
                    <ArrowUp className="h-3 w-3" />
                  )}
                </div>
              </th>
              <th className="px-4 py-3 text-left text-table-head">
                Signature
              </th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {filteredAndSortedLogs.map((log) => (
              <tr
                key={log.id}
                onClick={() => onViewDetails?.(log)}
                className={`hover:bg-muted/30 transition-colors ${onViewDetails ? 'cursor-pointer' : ''}`}
              >
                <td className="px-4 py-3 text-mono">
                  {format(parseTimestamp(log.timestamp), 'MMM d, HH:mm:ss')}
                </td>
                <td className="px-4 py-3">
                  <span className="text-sm text-foreground font-medium">
                    {log.decision}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-muted-foreground">
                  {log.agent_id}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`
                      px-2 py-0.5 text-xs rounded-md border
                      ${getWorkflowStateStyle(log.workflow_state)}
                    `}
                  >
                    {log.workflow_state.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <SignatureVerificationBadge
                    status={log.signature ? 'valid' : 'none'}
                    showLabel={false}
                  />
                </td>
                <td className="px-4 py-3">
                  {onViewDetails && (
                    <button
                      onClick={() => onViewDetails(log)}
                      className="
                        p-1.5 rounded-lg
                        hover:bg-muted transition-colors
                        text-muted-foreground hover:text-foreground
                      "
                    >
                      <Eye className="h-4 w-4" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {filteredAndSortedLogs.length === 0 && (
          <div className="px-4 py-12 text-center text-muted-foreground">
            No audit logs found matching your criteria
          </div>
        )}
      </div>
    </div>
  );
};
