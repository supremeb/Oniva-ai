'use client';

import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { subDays, format } from 'date-fns';
import {
  CurrencyDollar,
  ChartLine,
  Export,
  CaretDown,
  CircleNotch,
  TreeStructure,
  Users,
  Cpu,
  Lightning,
} from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  getCostSummary,
  getCostByWorkflow,
  getCostByUser,
  getCostByModel,
  getCostExportUrl,
  type CostSummaryResponse,
  type WorkflowCostBreakdown,
  type UserCostBreakdown,
  type ModelCostBreakdown,
} from '@/lib/api/analytics';
import { cn } from '@/lib/utils';

// --- Date Range Options ---
const DATE_RANGES = [
  { label: 'Last 7 days', days: 7 },
  { label: 'Last 30 days', days: 30 },
  { label: 'Last 90 days', days: 90 },
];

// --- Helper Functions ---
function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return value.toString();
}

// --- Cost Trends Chart (Simple SVG) ---
function CostTrendsChart({ data }: { data: CostSummaryResponse | undefined }) {
  if (!data || data.trends.length === 0) {
    return (
      <div className="h-48 flex items-center justify-center text-muted-foreground">
        <p className="text-sm">No cost data available for this period</p>
      </div>
    );
  }

  const { trends } = data;
  const maxCost = Math.max(...trends.map((t) => t.total_cost_usd), 0.01);

  return (
    <div className="h-48 relative">
      {/* Simple bar chart */}
      <div className="absolute inset-0 flex items-end gap-1 px-4 pb-6 pt-2">
        {trends.map((point, idx) => {
          const height = (point.total_cost_usd / maxCost) * 100;
          return (
            <div key={idx} className="flex-1 flex flex-col items-center group">
              <div
                className="w-full bg-primary/80 rounded-t hover:bg-primary transition-colors cursor-pointer relative"
                style={{ height: `${Math.max(height, 2)}%` }}
              >
                {/* Tooltip on hover */}
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                  <div className="bg-popover border rounded-md shadow-lg px-2 py-1 text-xs whitespace-nowrap">
                    <p className="font-medium">{formatCurrency(point.total_cost_usd)}</p>
                    <p className="text-muted-foreground">{formatTokens(point.total_tokens)} tokens</p>
                    <p className="text-muted-foreground">{point.api_calls} calls</p>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* X-axis labels */}
      <div className="absolute bottom-0 left-0 right-0 flex justify-between px-4 text-xs text-muted-foreground">
        <span>{format(new Date(trends[0].period), 'MMM d')}</span>
        {trends.length > 2 && (
          <span>{format(new Date(trends[Math.floor(trends.length / 2)].period), 'MMM d')}</span>
        )}
        <span>{format(new Date(trends[trends.length - 1].period), 'MMM d')}</span>
      </div>
    </div>
  );
}

// --- Main Page Component ---
export default function CostDashboard() {
  const [selectedRange, setSelectedRange] = useState(DATE_RANGES[1]); // Default: 30 days

  const dateRange = useMemo(() => {
    const end = new Date();
    const start = subDays(end, selectedRange.days);
    return { start, end };
  }, [selectedRange]);

  // Queries
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['cost-summary', dateRange.start.toISOString(), dateRange.end.toISOString()],
    queryFn: () => getCostSummary(dateRange.start, dateRange.end),
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  const { data: byWorkflow, isLoading: workflowLoading } = useQuery({
    queryKey: ['cost-by-workflow', dateRange.start.toISOString(), dateRange.end.toISOString()],
    queryFn: () => getCostByWorkflow(dateRange.start, dateRange.end),
    staleTime: 5 * 60 * 1000,
  });

  const { data: byUser, isLoading: userLoading } = useQuery({
    queryKey: ['cost-by-user', dateRange.start.toISOString(), dateRange.end.toISOString()],
    queryFn: () => getCostByUser(dateRange.start, dateRange.end),
    staleTime: 5 * 60 * 1000,
  });

  const { data: byModel, isLoading: modelLoading } = useQuery({
    queryKey: ['cost-by-model', dateRange.start.toISOString(), dateRange.end.toISOString()],
    queryFn: () => getCostByModel(dateRange.start, dateRange.end),
    staleTime: 5 * 60 * 1000,
  });

  const handleExport = () => {
    const url = getCostExportUrl(dateRange.start, dateRange.end);
    window.open(url, '_blank');
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-page-title flex items-center gap-2">
            <ChartLine className="h-6 w-6" />
            Cost Analytics
          </h1>
          <p className="text-page-description mt-1">
            Track and analyze LLM usage costs across workflows, users, and models
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Date Range Picker */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2">
                {selectedRange.label}
                <CaretDown className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {DATE_RANGES.map((range) => (
                <DropdownMenuItem
                  key={range.days}
                  onClick={() => setSelectedRange(range)}
                  className={cn(
                    selectedRange.days === range.days && 'bg-accent'
                  )}
                >
                  {range.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          {/* Export Button */}
          <Button variant="outline" size="sm" onClick={handleExport} className="gap-2">
            <Export className="h-4 w-4" />
            Export CSV
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1">
              <CurrencyDollar className="h-4 w-4" />
              Total Spend
            </CardDescription>
          </CardHeader>
          <CardContent>
            {summaryLoading ? (
              <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
            ) : (
              <>
                <p className="text-stat">{formatCurrency(summary?.total_cost_usd || 0)}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {formatCurrency(summary?.avg_cost_per_day || 0)}/day avg
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1">
              <Lightning className="h-4 w-4" />
              Tokens Used
            </CardDescription>
          </CardHeader>
          <CardContent>
            {summaryLoading ? (
              <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
            ) : (
              <>
                <p className="text-stat">{formatTokens(summary?.total_tokens || 0)}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Total input + output tokens
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1">
              <Cpu className="h-4 w-4" />
              API Calls
            </CardDescription>
          </CardHeader>
          <CardContent>
            {summaryLoading ? (
              <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
            ) : (
              <>
                <p className="text-stat">{(summary?.total_api_calls || 0).toLocaleString()}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  LLM requests made
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-1">
              <TreeStructure className="h-4 w-4" />
              Top Workflow
            </CardDescription>
          </CardHeader>
          <CardContent>
            {workflowLoading ? (
              <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
            ) : byWorkflow && byWorkflow.length > 0 ? (
              <>
                <p className="text-lg font-semibold truncate">
                  {byWorkflow[0].workflow_name || 'Unnamed'}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {formatCurrency(byWorkflow[0].total_cost_usd)} spent
                </p>
              </>
            ) : (
              <p className="text-muted-foreground text-sm">No workflows</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Trends Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Cost Trends</CardTitle>
          <CardDescription>Daily cost breakdown over the selected period</CardDescription>
        </CardHeader>
        <CardContent>
          {summaryLoading ? (
            <div className="h-48 flex items-center justify-center">
              <CircleNotch className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <CostTrendsChart data={summary} />
          )}
        </CardContent>
      </Card>

      {/* Breakdown Tables */}
      <Tabs defaultValue="workflows" className="space-y-4">
        <TabsList>
          <TabsTrigger value="workflows" className="gap-2">
            <TreeStructure className="h-4 w-4" />
            By Workflow
          </TabsTrigger>
          <TabsTrigger value="users" className="gap-2">
            <Users className="h-4 w-4" />
            By User
          </TabsTrigger>
          <TabsTrigger value="models" className="gap-2">
            <Cpu className="h-4 w-4" />
            By Model
          </TabsTrigger>
        </TabsList>

        {/* By Workflow */}
        <TabsContent value="workflows">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Cost by Workflow</CardTitle>
              <CardDescription>Top workflows by total cost</CardDescription>
            </CardHeader>
            <CardContent>
              {workflowLoading ? (
                <div className="flex justify-center py-8">
                  <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : !byWorkflow || byWorkflow.length === 0 ? (
                <p className="text-center py-8 text-muted-foreground">No workflow data</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Workflow</TableHead>
                      <TableHead className="text-right">Executions</TableHead>
                      <TableHead className="text-right">Tokens</TableHead>
                      <TableHead className="text-right">Avg Cost</TableHead>
                      <TableHead className="text-right">Total Cost</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {byWorkflow.map((item, idx) => (
                      <TableRow key={item.workflow_id || idx}>
                        <TableCell className="font-medium">
                          {item.workflow_name || 'Unnamed Workflow'}
                        </TableCell>
                        <TableCell className="text-right">
                          {item.execution_count.toLocaleString()}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatTokens(item.total_tokens)}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatCurrency(item.avg_cost_per_execution)}
                        </TableCell>
                        <TableCell className="text-right font-semibold">
                          {formatCurrency(item.total_cost_usd)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* By User */}
        <TabsContent value="users">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Cost by User</CardTitle>
              <CardDescription>Cost attribution by team member</CardDescription>
            </CardHeader>
            <CardContent>
              {userLoading ? (
                <div className="flex justify-center py-8">
                  <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : !byUser || byUser.length === 0 ? (
                <p className="text-center py-8 text-muted-foreground">No user data</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>User</TableHead>
                      <TableHead className="text-right">Workflows</TableHead>
                      <TableHead className="text-right">Tokens</TableHead>
                      <TableHead className="text-right">Total Cost</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {byUser.map((item) => (
                      <TableRow key={item.user_id}>
                        <TableCell>
                          <div>
                            <p className="font-medium">{item.user_name || 'Unknown'}</p>
                            <p className="text-xs text-muted-foreground">{item.user_email}</p>
                          </div>
                        </TableCell>
                        <TableCell className="text-right">{item.workflow_count}</TableCell>
                        <TableCell className="text-right">
                          {formatTokens(item.total_tokens)}
                        </TableCell>
                        <TableCell className="text-right font-semibold">
                          {formatCurrency(item.total_cost_usd)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* By Model */}
        <TabsContent value="models">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Cost by Model</CardTitle>
              <CardDescription>Usage breakdown by LLM provider and model</CardDescription>
            </CardHeader>
            <CardContent>
              {modelLoading ? (
                <div className="flex justify-center py-8">
                  <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : !byModel || byModel.length === 0 ? (
                <p className="text-center py-8 text-muted-foreground">No model data</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Provider / Model</TableHead>
                      <TableHead className="text-right">API Calls</TableHead>
                      <TableHead className="text-right">Tokens</TableHead>
                      <TableHead className="text-right">Avg/Call</TableHead>
                      <TableHead className="text-right">Total Cost</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {byModel.map((item, idx) => (
                      <TableRow key={`${item.provider_name}-${item.model}-${idx}`}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Badge variant="outline" className="text-xs">
                              {item.provider_name}
                            </Badge>
                            <span className="font-medium">{item.model}</span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right">
                          {item.api_calls.toLocaleString()}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatTokens(item.total_tokens)}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatCurrency(item.avg_cost_per_call)}
                        </TableCell>
                        <TableCell className="text-right font-semibold">
                          {formatCurrency(item.total_cost_usd)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
