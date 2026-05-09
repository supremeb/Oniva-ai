'use client';

import { memo, useState, useCallback, useMemo } from 'react';
import { cn } from '@/lib/utils';
import {
  CaretUp,
  CaretDown,
  CaretUpDown,
  Copy,
  Check,
  DownloadSimple,
  MagnifyingGlass,
} from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { SpringCard } from '../primitives/SpringCard';
import { animations, transitions } from '@/lib/animation-utils';

export interface DataTableColumn {
  key: string;
  header: string;
  width?: string | number;
  align?: 'left' | 'center' | 'right';
  sortable?: boolean;
  render?: (value: unknown, row: Record<string, unknown>) => React.ReactNode;
}

export interface DataTableProps {
  /** Table columns */
  columns: DataTableColumn[];
  /** Table data */
  data: Record<string, unknown>[];
  /** Enable sorting */
  sortable?: boolean;
  /** Enable search */
  searchable?: boolean;
  /** Search placeholder */
  searchPlaceholder?: string;
  /** Enable row selection */
  selectable?: boolean;
  /** Selected row keys */
  selectedKeys?: Set<string>;
  /** Row key field */
  rowKey?: string;
  /** Maximum visible rows before scrolling */
  maxRows?: number;
  /** Compact mode */
  compact?: boolean;
  /** Show row numbers */
  showRowNumbers?: boolean;
  /** Enable copy to clipboard */
  copyable?: boolean;
  /** Enable CSV export */
  exportable?: boolean;
  /** Table title */
  title?: string;
  /** Loading state */
  loading?: boolean;
  /** Empty state message */
  emptyMessage?: string;
  /** Callback on row click */
  onRowClick?: (row: Record<string, unknown>, index: number) => void;
  /** Callback on selection change */
  onSelectionChange?: (keys: Set<string>) => void;
  /** Additional CSS classes */
  className?: string;
}

type SortDirection = 'asc' | 'desc' | null;

/**
 * DataTable - Professional data table with sorting and filtering
 *
 * Features:
 * - Column sorting
 * - Search/filter
 * - Row selection
 * - Copy to clipboard
 * - CSV export
 */
export const DataTable = memo(function DataTable({
  columns,
  data,
  sortable = true,
  searchable = false,
  searchPlaceholder = 'Search...',
  selectable = false,
  selectedKeys = new Set(),
  rowKey = 'id',
  maxRows = 10,
  compact = false,
  showRowNumbers = false,
  copyable = true,
  exportable = false,
  title,
  loading = false,
  emptyMessage = 'No data available',
  onRowClick,
  onSelectionChange,
  className,
}: DataTableProps) {
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [copied, setCopied] = useState(false);

  // Filter data
  const filteredData = useMemo(() => {
    if (!searchQuery.trim()) return data;

    const query = searchQuery.toLowerCase();
    return data.filter((row) =>
      columns.some((col) => {
        const value = row[col.key];
        return String(value).toLowerCase().includes(query);
      })
    );
  }, [data, columns, searchQuery]);

  // Sort data
  const sortedData = useMemo(() => {
    if (!sortColumn || !sortDirection) return filteredData;

    return [...filteredData].sort((a, b) => {
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
  }, [filteredData, sortColumn, sortDirection]);

  // Handle sort
  const handleSort = useCallback(
    (columnKey: string) => {
      if (!sortable) return;

      if (sortColumn === columnKey) {
        if (sortDirection === 'asc') {
          setSortDirection('desc');
        } else if (sortDirection === 'desc') {
          setSortColumn(null);
          setSortDirection(null);
        }
      } else {
        setSortColumn(columnKey);
        setSortDirection('asc');
      }
    },
    [sortable, sortColumn, sortDirection]
  );

  // Handle row selection
  const handleRowSelect = useCallback(
    (key: string) => {
      if (!selectable) return;

      const newSelected = new Set(selectedKeys);
      if (newSelected.has(key)) {
        newSelected.delete(key);
      } else {
        newSelected.add(key);
      }
      onSelectionChange?.(newSelected);
    },
    [selectable, selectedKeys, onSelectionChange]
  );

  // Handle select all
  const handleSelectAll = useCallback(() => {
    if (!selectable) return;

    if (selectedKeys.size === sortedData.length) {
      onSelectionChange?.(new Set());
    } else {
      const allKeys = new Set(sortedData.map((row) => String(row[rowKey])));
      onSelectionChange?.(allKeys);
    }
  }, [selectable, selectedKeys, sortedData, rowKey, onSelectionChange]);

  // Copy to clipboard
  const handleCopy = useCallback(async () => {
    const headers = columns.map((c) => c.header).join('\t');
    const rows = sortedData.map((row) =>
      columns.map((c) => String(row[c.key] ?? '')).join('\t')
    );
    const text = [headers, ...rows].join('\n');

    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [columns, sortedData]);

  // Export to CSV
  const handleExport = useCallback(() => {
    const headers = columns.map((c) => `"${c.header}"`).join(',');
    const rows = sortedData.map((row) =>
      columns.map((c) => `"${String(row[c.key] ?? '').replace(/"/g, '""')}"`).join(',')
    );
    const csv = [headers, ...rows].join('\n');

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `export-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [columns, sortedData]);

  const cellPadding = compact ? 'px-2 py-1.5' : 'px-3 py-2';
  const fontSize = compact ? 'text-xs' : 'text-sm';

  return (
    <FadeSlide>
      <SpringCard variant="outline" size={compact ? 'sm' : 'md'} className={className}>
        {/* Header */}
        {(title || searchable || copyable || exportable) && (
          <div className="flex items-center justify-between gap-3 mb-3">
            {title && (
              <h4 className={cn('font-semibold', fontSize)}>{title}</h4>
            )}

            <div className="flex items-center gap-2 flex-1 justify-end">
              {/* Search */}
              {searchable && (
                <div className="relative max-w-xs flex-1">
                  <MagnifyingGlass className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder={searchPlaceholder}
                    className={cn(
                      'w-full pl-7 pr-3 py-1.5 rounded-md border border-input bg-background',
                      'text-xs placeholder:text-muted-foreground/60',
                      'focus:outline-none focus:ring-2 focus:ring-ring',
                      transitions.colors
                    )}
                  />
                </div>
              )}

              {/* Copy button */}
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

              {/* Export button */}
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
        )}

        {/* Table */}
        <div
          className="overflow-auto rounded-md border border-border"
          style={{ maxHeight: maxRows ? `${maxRows * (compact ? 32 : 40) + 40}px` : undefined }}
        >
          <table className="w-full">
            <thead className="bg-muted/50 sticky top-0">
              <tr>
                {/* Select all checkbox */}
                {selectable && (
                  <th className={cn(cellPadding, 'w-10')}>
                    <input
                      type="checkbox"
                      checked={selectedKeys.size === sortedData.length && sortedData.length > 0}
                      onChange={handleSelectAll}
                      className="rounded border-muted-foreground/30"
                    />
                  </th>
                )}

                {/* Row number */}
                {showRowNumbers && (
                  <th className={cn(cellPadding, 'w-10 text-muted-foreground', fontSize)}>
                    #
                  </th>
                )}

                {/* Column headers */}
                {columns.map((col) => (
                  <th
                    key={col.key}
                    className={cn(
                      cellPadding,
                      fontSize,
                      'font-medium text-foreground whitespace-nowrap',
                      col.align === 'center' && 'text-center',
                      col.align === 'right' && 'text-right',
                      sortable && col.sortable !== false && 'cursor-pointer hover:bg-muted/50',
                      transitions.colors
                    )}
                    style={{ width: col.width }}
                    onClick={() => col.sortable !== false && handleSort(col.key)}
                  >
                    <div className="flex items-center gap-1">
                      <span>{col.header}</span>
                      {sortable && col.sortable !== false && (
                        <span className="text-muted-foreground">
                          {sortColumn === col.key ? (
                            sortDirection === 'asc' ? (
                              <CaretUp className="h-3.5 w-3.5" weight="bold" />
                            ) : (
                              <CaretDown className="h-3.5 w-3.5" weight="bold" />
                            )
                          ) : (
                            <CaretUpDown className="h-3.5 w-3.5 opacity-30" />
                          )}
                        </span>
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>

            <tbody>
              {loading ? (
                <tr>
                  <td
                    colSpan={columns.length + (selectable ? 1 : 0) + (showRowNumbers ? 1 : 0)}
                    className={cn(cellPadding, fontSize, 'text-center text-muted-foreground')}
                  >
                    Loading...
                  </td>
                </tr>
              ) : sortedData.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length + (selectable ? 1 : 0) + (showRowNumbers ? 1 : 0)}
                    className={cn(cellPadding, fontSize, 'text-center text-muted-foreground')}
                  >
                    {emptyMessage}
                  </td>
                </tr>
              ) : (
                sortedData.map((row, index) => {
                  const key = String(row[rowKey] ?? index);
                  const isSelected = selectedKeys.has(key);

                  return (
                    <tr
                      key={key}
                      className={cn(
                        'border-t border-border',
                        isSelected && 'bg-primary/5',
                        onRowClick && 'cursor-pointer hover:bg-muted/50',
                        transitions.colors
                      )}
                      onClick={() => onRowClick?.(row, index)}
                    >
                      {/* Selection checkbox */}
                      {selectable && (
                        <td className={cellPadding} onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => handleRowSelect(key)}
                            className="rounded border-muted-foreground/30"
                          />
                        </td>
                      )}

                      {/* Row number */}
                      {showRowNumbers && (
                        <td className={cn(cellPadding, fontSize, 'text-muted-foreground')}>
                          {index + 1}
                        </td>
                      )}

                      {/* Data cells */}
                      {columns.map((col) => (
                        <td
                          key={col.key}
                          className={cn(
                            cellPadding,
                            fontSize,
                            col.align === 'center' && 'text-center',
                            col.align === 'right' && 'text-right'
                          )}
                        >
                          {col.render
                            ? col.render(row[col.key], row)
                            : String(row[col.key] ?? '-')}
                        </td>
                      ))}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Footer */}
        {sortedData.length > 0 && (
          <div className="mt-2 text-xs text-muted-foreground">
            {filteredData.length !== data.length
              ? `Showing ${sortedData.length} of ${data.length} rows`
              : `${sortedData.length} rows`}
            {selectedKeys.size > 0 && ` • ${selectedKeys.size} selected`}
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { DataTableProps as DataTableComponentProps };
