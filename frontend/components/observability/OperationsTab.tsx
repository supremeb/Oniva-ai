'use client';

import { Fragment, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { CircleNotch, Timer, Warning, CheckCircle, Database, CaretDown, CaretRight } from '@phosphor-icons/react';
import { useDashboardMetrics } from '@/hooks/useObservability';
import type { ObservabilityDashboardMetrics } from '@/lib/api/observability';
import { cn } from '@/lib/utils';

function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toString();
}

function formatCost(usd: number): string {
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  if (usd >= 0.01) return `$${usd.toFixed(3)}`;
  if (usd === 0) return '$0.00';
  return `$${usd.toFixed(4)}`;
}

interface OperationsTabProps {
  windowMinutes: number;
}

export function OperationsTab({ windowMinutes }: OperationsTabProps) {
  const { data, isLoading, error } = useDashboardMetrics(windowMinutes);
  const [expandedModels, setExpandedModels] = useState<Set<string>>(new Set());

  const toggleModel = (group: string) => {
    setExpandedModels((prev) => {
      const next = new Set(prev);
      if (next.has(group)) {
        next.delete(group);
      } else {
        next.add(group);
      }
      return next;
    });
  };

  if (error) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <Warning className="h-8 w-8 mx-auto mb-2" />
        <p className="text-sm">Failed to load metrics</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <CircleNotch className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1">
              <Timer className="h-4 w-4" />
              P50 Latency
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold">{formatMs(data.latency.p50)}</p>
            <p className="text-xs text-muted-foreground mt-1">
              P95: {formatMs(data.latency.p95)} / P99: {formatMs(data.latency.p99)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1">
              <CheckCircle className="h-4 w-4" />
              Success Rate
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold">
              {(data.error_rate.success_rate * 100).toFixed(1)}%
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {data.error_rate.error_count} errors / {data.error_rate.total_calls} calls
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1">
              <Warning className="h-4 w-4" />
              Error Rate
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold">
              {(data.error_rate.error_rate * 100).toFixed(2)}%
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {data.error_rate.total_calls} total LLM calls
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1">
              <Database className="h-4 w-4" />
              Mean Latency
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold">{formatMs(data.latency.mean)}</p>
            <p className="text-xs text-muted-foreground mt-1">
              Across {data.latency.count} calls
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Token Usage by Model */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Token Usage by Model</CardTitle>
          <CardDescription>
            LLM token consumption in the last {windowMinutes >= 60 ? `${windowMinutes / 60}h` : `${windowMinutes}m`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {data.token_usage_by_model.length === 0 ? (
            <p className="text-center py-8 text-muted-foreground text-sm">
              No token usage data in this window
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8"></TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead className="text-right">Calls</TableHead>
                  <TableHead className="text-right">Input Tokens</TableHead>
                  <TableHead className="text-right">Output Tokens</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                  <TableHead className="text-right">Avg Latency</TableHead>
                  <TableHead className="text-right">Cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.token_usage_by_model.map((row) => {
                  const isExpanded = expandedModels.has(row.group);
                  const costPerKToken = row.total_tokens > 0
                    ? (row.cost_usd / row.total_tokens) * 1000
                    : 0;
                  const inputRatio = row.total_tokens > 0
                    ? ((row.input_tokens / row.total_tokens) * 100).toFixed(0)
                    : '0';
                  const outputRatio = row.total_tokens > 0
                    ? ((row.output_tokens / row.total_tokens) * 100).toFixed(0)
                    : '0';
                  const tokensPerCall = row.calls > 0
                    ? Math.round(row.total_tokens / row.calls)
                    : 0;
                  const costPerCall = row.calls > 0
                    ? row.cost_usd / row.calls
                    : 0;

                  return (
                    <Fragment key={row.group}>
                      <TableRow className="cursor-pointer hover:bg-accent/50" onClick={() => toggleModel(row.group)}>
                        <TableCell className="w-8 px-2">
                          {isExpanded ? (
                            <CaretDown className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <CaretRight className="h-4 w-4 text-muted-foreground" />
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs font-mono">
                            {row.group}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">{row.calls.toLocaleString()}</TableCell>
                        <TableCell className="text-right">{formatTokens(row.input_tokens)}</TableCell>
                        <TableCell className="text-right">{formatTokens(row.output_tokens)}</TableCell>
                        <TableCell className="text-right font-semibold">
                          {formatTokens(row.total_tokens)}
                        </TableCell>
                        <TableCell className="text-right text-xs font-mono">
                          {formatMs(row.avg_latency_ms)}
                        </TableCell>
                        <TableCell className="text-right text-xs font-mono">
                          {formatCost(row.cost_usd)}
                        </TableCell>
                      </TableRow>
                      {isExpanded && (
                        <tr>
                          <td colSpan={8} className="p-0">
                            <div className="bg-muted/30 border-t px-6 py-4">
                              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                <div>
                                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Cost per 1K tokens</p>
                                  <p className="text-sm font-mono font-medium">{formatCost(costPerKToken)}</p>
                                </div>
                                <div>
                                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Cost per call</p>
                                  <p className="text-sm font-mono font-medium">{formatCost(costPerCall)}</p>
                                </div>
                                <div>
                                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Tokens per call</p>
                                  <p className="text-sm font-mono font-medium">{tokensPerCall.toLocaleString()}</p>
                                </div>
                                <div>
                                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Input / Output ratio</p>
                                  <p className="text-sm font-mono font-medium">{inputRatio}% / {outputRatio}%</p>
                                </div>
                              </div>
                              {/* Token distribution bar */}
                              <div className="mt-3">
                                <div className="flex h-2 rounded-full overflow-hidden bg-muted">
                                  <div
                                    className="bg-blue-500/70 transition-all"
                                    style={{ width: `${inputRatio}%` }}
                                    title={`Input: ${formatTokens(row.input_tokens)}`}
                                  />
                                  <div
                                    className="bg-emerald-500/70 transition-all"
                                    style={{ width: `${outputRatio}%` }}
                                    title={`Output: ${formatTokens(row.output_tokens)}`}
                                  />
                                </div>
                                <div className="flex justify-between mt-1">
                                  <span className="text-[10px] text-blue-600 dark:text-blue-400">Input {formatTokens(row.input_tokens)}</span>
                                  <span className="text-[10px] text-emerald-600 dark:text-emerald-400">Output {formatTokens(row.output_tokens)}</span>
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
