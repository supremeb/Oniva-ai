'use client';

import { memo, useState, useCallback, useMemo } from 'react';
import { cn } from '@/lib/utils';
import {
  Database,
  CaretUp,
  CaretDown,
  Copy,
  Check,
  DownloadSimple,
  ArrowLeft,
  ArrowRight,
} from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { SpringCard } from '../primitives/SpringCard';
import { animations, transitions } from '@/lib/animation-utils';

export interface SQLResultColumn {
  name: string;
  type?: string;
  nullable?: boolean;
}

export interface SQLResultTableProps {
  /** Column definitions */
  columns: SQLResultColumn[];
  /** Row data */
  rows: Record<string, unknown>[];
  /** Query that produced these results */
  query?: string;
  /** Execution time in milliseconds */
  executionTime?: number;
  /** Total row count (may differ from rows.length if paginated) */
  totalRows?: number;
  /** Current page (1-indexed) */
  page?: number;
  /** Rows per page */
  pageSize?: number;
  /** Enable pagination */
  paginated?: boolean;
  /** Enable sorting */
  sortable?: boolean;
  /** Enable copy */
  copyable?: boolean;
  /** Enable CSV export */
  exportable?: boolean;
  /** Compact mode */
  compact?: boolean;
  /** Maximum visible rows before scrolling */
  maxRows?: number;
  /** Null value display */
  nullDisplay?: string;
  /** Callback on page change */
  onPageChange?: (page: number) => void;
  /** Additional CSS classes */
  className?: string;
}

type SortDirection = 'asc' | 'desc' | null;

/**
 * SQLResultTable - Display SQL query results
 *
 * Features:
 * - Column types
 * - Sorting
 * - Pagination
 * - Copy to clipboard
 * - CSV export
 * - Null value handling
 */
export const SQLResultTable = memo(function SQLResultTable({
  columns,
  rows,
  query,
  executionTime,
  totalRows,
  page = 1,
  pageSize = 50,
  paginated = false,
  sortable = true,
  copyable = true,
  exportable = true,
  compact = false,
  maxRows = 10,
  nullDisplay = 'NULL',
  onPageChange,
  className,
}: SQLResultTableProps) {
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>(null);
  const [copied, setCopied] = useState(false);

  // Sort data
  const sortedRows = useMemo(() => {
    if (!sortColumn || !sortDirection) return rows;

    return [...rows].sort((a, b) => {
      const aVal = a[sortColumn];
      const bVal = b[sortColumn];

      if (aVal === null || aVal === undefined) return 1;
      if (bVal === null || bVal === undefined) return -1;

      let comparison = 0;
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        comparison = aVal - bVal;
      } else {
        comparison = String(aVal).localeCompare(String(bVal));
      }

      return sortDirection === 'asc' ? comparison : -comparison;
    });
  }, [rows, sortColumn, sortDirection]);

  // Handle sort
  const handleSort = useCallback((columnName: string) => {
    if (!sortable) return;

    if (sortColumn === columnName) {
      if (sortDirection === 'asc') {
        setSortDirection('desc');
      } else if (sortDirection === 'desc') {
        setSortColumn(null);
        setSortDirection(null);
      }
    } else {
      setSortColumn(columnName);
      setSortDirection('asc');
    }
  }, [sortable, sortColumn, sortDirection]);

  // Copy to clipboard
  const handleCopy = useCallback(async () => {
    const headers = columns.map((c) => c.name).join('\t');
    const data = sortedRows.map((row) =>
      columns.map((c) => {
        const val = row[c.name];
        return val === null || val === undefined ? '' : String(val);
      }).join('\t')
    );
    const text = [headers, ...data].join('\n');

    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [columns, sortedRows]);

  // Export to CSV
  const handleExport = useCallback(() => {
    const headers = columns.map((c) => `"${c.name}"`).join(',');
    const data = sortedRows.map((row) =>
      columns.map((c) => {
        const val = row[c.name];
        if (val === null || val === undefined) return '""';
        return `"${String(val).replace(/"/g, '""')}"`;
      }).join(',')
    );
    const csv = [headers, ...data].join('\n');

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `query-results-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [columns, sortedRows]);

  // Format cell value
  const formatValue = (value: unknown, type?: string): React.ReactNode => {
    if (value === null || value === undefined) {
      return <span className="text-neutral-500 italic">{nullDisplay}</span>;
    }

    if (typeof value === 'boolean') {
      return (
        <span className={value ? 'text-green-400' : 'text-red-400'}>
          {String(value)}
        </span>
      );
    }

    if (typeof value === 'number') {
      return <span className="text-orange-400 font-mono">{value}</span>;
    }

    if (value instanceof Date || (type && type.toLowerCase().includes('date'))) {
      return <span className="font-mono">{String(value)}</span>;
    }

    return String(value);
  };

  const cellPadding = compact ? 'px-2 py-1.5' : 'px-3 py-2';
  const fontSize = compact ? 'text-xs' : 'text-sm';
  const actualTotalRows = totalRows ?? rows.length;
  const totalPages = Math.ceil(actualTotalRows / pageSize);

  return (
    <FadeSlide>
      <SpringCard variant="outline" size={compact ? 'sm' : 'md'} className={className}>
        {/* Header */}
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" weight="fill" />
            <span className={cn('font-medium', fontSize)}>Query Results</span>
            <span className="text-xs text-muted-foreground">
              {actualTotalRows} rows
              {executionTime !== undefined && ` • ${executionTime}ms`}
            </span>
          </div>

          <div className="flex items-center gap-1">
            {copyable && (
              <button
                onClick={handleCopy}
                className={cn(
                  'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                  animations.press,
                  transitions.colors
                )}
                title="Copy to clipboard"
              >
                {copied ? (
                  <Check className="h-4 w-4 text-green-500" weight="bold" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            )}
            {exportable && (
              <button
                onClick={handleExport}
                className={cn(
                  'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                  animations.press,
                  transitions.colors
                )}
                title="Export to CSV"
              >
                <DownloadSimple className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* Table */}
        <div
          className="overflow-auto rounded-md border border-border"
          style={{ maxHeight: maxRows ? `${maxRows * (compact ? 32 : 40) + 40}px` : undefined }}
        >
          <table className="w-full">
            <thead className="bg-muted/50 sticky top-0">
              <tr>
                {columns.map((col) => (
                  <th
                    key={col.name}
                    className={cn(
                      cellPadding,
                      fontSize,
                      'font-medium text-left whitespace-nowrap',
                      sortable && 'cursor-pointer hover:bg-muted/70',
                      transitions.colors
                    )}
                    onClick={() => handleSort(col.name)}
                  >
                    <div className="flex items-center gap-1">
                      <span>{col.name}</span>
                      {col.type && (
                        <span className="text-xs text-muted-foreground font-normal">
                          ({col.type})
                        </span>
                      )}
                      {sortable && (
                        <span className="text-muted-foreground">
                          {sortColumn === col.name ? (
                            sortDirection === 'asc' ? (
                              <CaretUp className="h-3.5 w-3.5" weight="bold" />
                            ) : (
                              <CaretDown className="h-3.5 w-3.5" weight="bold" />
                            )
                          ) : null}
                        </span>
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>

            <tbody>
              {sortedRows.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className={cn(cellPadding, fontSize, 'text-center text-muted-foreground')}
                  >
                    No results
                  </td>
                </tr>
              ) : (
                sortedRows.map((row, rowIndex) => (
                  <tr
                    key={rowIndex}
                    className={cn(
                      'border-t border-border',
                      'hover:bg-muted/30',
                      transitions.colors
                    )}
                  >
                    {columns.map((col) => (
                      <td
                        key={col.name}
                        className={cn(cellPadding, fontSize, 'font-mono')}
                      >
                        {formatValue(row[col.name], col.type)}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {paginated && totalPages > 1 && (
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-border">
            <span className="text-xs text-muted-foreground">
              Showing {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, actualTotalRows)} of {actualTotalRows}
            </span>

            <div className="flex items-center gap-1">
              <button
                onClick={() => onPageChange?.(page - 1)}
                disabled={page <= 1}
                className={cn(
                  'p-1 rounded text-muted-foreground',
                  'hover:text-foreground hover:bg-muted',
                  'disabled:opacity-50 disabled:cursor-not-allowed',
                  animations.press,
                  transitions.colors
                )}
              >
                <ArrowLeft className="h-4 w-4" />
              </button>

              <span className="px-2 text-xs">
                Page {page} of {totalPages}
              </span>

              <button
                onClick={() => onPageChange?.(page + 1)}
                disabled={page >= totalPages}
                className={cn(
                  'p-1 rounded text-muted-foreground',
                  'hover:text-foreground hover:bg-muted',
                  'disabled:opacity-50 disabled:cursor-not-allowed',
                  animations.press,
                  transitions.colors
                )}
              >
                <ArrowRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { SQLResultTableProps as SQLResultTableComponentProps };
