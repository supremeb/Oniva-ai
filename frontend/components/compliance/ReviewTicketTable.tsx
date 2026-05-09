'use client';

import { type FC, useState, useMemo } from 'react';
import { format } from 'date-fns';
import { parseTimestamp } from '@/lib/utils';
import {
  ArrowUp,
  ArrowDown,
  FunnelSimple,
  MagnifyingGlass,
  Eye,
  User,
  CheckCircle,
  XCircle,
} from '@phosphor-icons/react';
import type { ReviewTicket } from '@/lib/api/compliance';

interface ReviewTicketTableProps {
  tickets: ReviewTicket[];
  isLoading?: boolean;
  onAssign?: (ticketId: string) => void;
  onApprove?: (ticketId: string) => void;
  onReject?: (ticketId: string) => void;
  onViewDetails?: (ticket: ReviewTicket) => void;
}

export const ReviewTicketTable: FC<ReviewTicketTableProps> = ({
  tickets,
  isLoading = false,
  onAssign,
  onApprove,
  onReject,
  onViewDetails,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState<keyof ReviewTicket>('created_at');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  const [priorityFilter, setPriorityFilter] = useState<string>('ALL');

  const filteredAndSortedTickets = useMemo(() => {
    let filtered = tickets;

    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(
        (t) =>
          t.title.toLowerCase().includes(term) ||
          t.agent_id.toLowerCase().includes(term) ||
          t.description.toLowerCase().includes(term)
      );
    }

    if (priorityFilter !== 'ALL') {
      filtered = filtered.filter((t) => t.priority.toLowerCase() === priorityFilter.toLowerCase());
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
  }, [tickets, searchTerm, sortField, sortDirection, priorityFilter]);

  const handleSort = (field: keyof ReviewTicket) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  const getPriorityStyle = (priority: string) => {
    switch (priority.toUpperCase()) {
      case 'URGENT':
        return 'bg-muted text-foreground border-border font-semibold';
      case 'HIGH':
        return 'bg-muted text-foreground border-border font-medium';
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
            placeholder="Search by title, agent, or description..."
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
              value={priorityFilter}
              onChange={(e) => setPriorityFilter(e.target.value)}
              className="
                bg-transparent text-sm text-foreground
                focus:outline-none cursor-pointer
              "
            >
              <option value="ALL">All Priorities</option>
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-border bg-card">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th className="px-4 py-3 text-left text-table-head">
                Status
              </th>
              <th className="px-4 py-3 text-left text-table-head">
                Title
              </th>
              <th
                onClick={() => handleSort('priority')}
                className="px-4 py-3 text-left text-table-head cursor-pointer hover:text-foreground transition-colors"
              >
                <div className="flex items-center gap-1">
                  Priority
                  {sortField === 'priority' && (
                    sortDirection === 'desc' ?
                    <ArrowDown className="h-3 w-3" /> :
                    <ArrowUp className="h-3 w-3" />
                  )}
                </div>
              </th>
              <th className="px-4 py-3 text-left text-table-head">
                Risk
              </th>
              <th className="px-4 py-3 text-left text-table-head">
                Type
              </th>
              <th className="px-4 py-3 text-left text-table-head">
                Agent
              </th>
              <th
                onClick={() => handleSort('created_at')}
                className="px-4 py-3 text-left text-table-head cursor-pointer hover:text-foreground transition-colors"
              >
                <div className="flex items-center gap-1">
                  Created
                  {sortField === 'created_at' && (
                    sortDirection === 'desc' ?
                    <ArrowDown className="h-3 w-3" /> :
                    <ArrowUp className="h-3 w-3" />
                  )}
                </div>
              </th>
              <th className="px-4 py-3 text-right text-table-head">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {filteredAndSortedTickets.map((ticket) => (
              <tr
                key={ticket.id}
                onClick={() => onViewDetails?.(ticket)}
                className={`hover:bg-muted/30 transition-colors ${onViewDetails ? 'cursor-pointer' : ''}`}
              >
                <td className="px-4 py-3">
                  <span className="text-xs text-muted-foreground capitalize">
                    {ticket.status.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div>
                    <span className="text-sm text-foreground font-medium">
                      {ticket.title}
                    </span>
                    <p className="text-mono-sm mt-0.5">
                      {ticket.id.slice(0, 8)}...
                    </p>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`
                      px-2 py-0.5 text-xs rounded-md border
                      ${getPriorityStyle(ticket.priority)}
                    `}
                  >
                    {ticket.priority.toUpperCase()}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="text-xs text-muted-foreground uppercase">
                    {ticket.risk_level}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="text-xs text-muted-foreground">
                    {ticket.review_type.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-muted-foreground">
                  {ticket.agent_id}
                </td>
                <td className="px-4 py-3 text-mono">
                  {format(parseTimestamp(ticket.created_at), 'MMM d, HH:mm')}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1">
                    {!['approved', 'rejected'].includes(ticket.status.toLowerCase()) && (
                      <>
                        {onAssign && (
                          <button
                            onClick={() => onAssign(ticket.id)}
                            className="
                              p-1.5 rounded-lg
                              hover:bg-muted transition-colors
                              text-muted-foreground hover:text-foreground
                            "
                            title="Assign"
                          >
                            <User className="h-4 w-4" />
                          </button>
                        )}
                        {onApprove && (
                          <button
                            onClick={() => onApprove(ticket.id)}
                            className="
                              p-1.5 rounded-lg
                              hover:bg-muted transition-colors
                              text-muted-foreground hover:text-emerald-600
                            "
                            title="Approve"
                          >
                            <CheckCircle className="h-4 w-4" />
                          </button>
                        )}
                        {onReject && (
                          <button
                            onClick={() => onReject(ticket.id)}
                            className="
                              p-1.5 rounded-lg
                              hover:bg-muted transition-colors
                              text-muted-foreground hover:text-red-600
                            "
                            title="Reject"
                          >
                            <XCircle className="h-4 w-4" />
                          </button>
                        )}
                      </>
                    )}
                    {onViewDetails && (
                      <button
                        onClick={() => onViewDetails(ticket)}
                        className="
                          p-1.5 rounded-lg
                          hover:bg-muted transition-colors
                          text-muted-foreground hover:text-foreground
                        "
                        title="View details"
                      >
                        <Eye className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {filteredAndSortedTickets.length === 0 && (
          <div className="px-4 py-12 text-center text-muted-foreground">
            No review tickets found matching your criteria
          </div>
        )}
      </div>
    </div>
  );
};
