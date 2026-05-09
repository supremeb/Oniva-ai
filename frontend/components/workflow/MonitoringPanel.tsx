'use client';

import { useState, useMemo } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { ChartLine, Users, Coins, ChatCircle, CircleNotch } from '@phosphor-icons/react';
import { useMonitoringMetrics, useMonitoringTimeSeries } from '@/hooks/useWorkflowLogs';
import type { TimeRange } from '@/lib/api/workflow-logs';

// Color mapping for chart strokes (H8: proper constant dictionary)
const CHART_COLORS: Record<string, string> = {
    'text-blue-500': '#3b82f6',
    'text-green-500': '#22c55e',
    'text-red-500': '#ef4444',
    'text-orange-500': '#f97316',
};

interface MetricCardProps {
    title: string;
    value: string | number;
    change?: string;
    icon: React.ElementType;
    color: string;
    data?: number[];
    isLoading?: boolean;
}

function MiniChart({ data, color }: { data: number[]; color: string }) {
    if (!data || data.length === 0) return null;

    const max = Math.max(...data);
    const min = Math.min(...data);
    const range = max - min || 1;
    const height = 40;
    const width = 120;
    const points = data.map((v, i) => {
        const x = (i / (data.length - 1)) * width;
        const y = height - ((v - min) / range) * height;
        return `${x},${y}`;
    }).join(' ');

    return (
        <svg width={width} height={height} className="opacity-60" aria-hidden="true">
            <polyline
                points={points}
                fill="none"
                stroke={color}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
            />
        </svg>
    );
}

function MetricCard({ title, value, change, icon: Icon, color, data, isLoading }: MetricCardProps) {
    const isPositive = change?.startsWith('+');

    return (
        <Card>
            <CardContent className="p-4">
                <div className="flex items-start justify-between">
                    <div>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                            <Icon className={`h-4 w-4 ${color}`} weight="duotone" />
                            <span>{title}</span>
                        </div>
                        {isLoading ? (
                            <div className="h-8 flex items-center">
                                <CircleNotch className="h-5 w-5 animate-spin text-muted-foreground" />
                            </div>
                        ) : (
                            <div className="text-2xl font-semibold">{value}</div>
                        )}
                        {change && !isLoading && (
                            <div className={`text-xs mt-1 ${isPositive ? 'text-green-600' : 'text-red-600'}`}>
                                {change} from last period
                            </div>
                        )}
                    </div>
                    <MiniChart data={data || []} color={CHART_COLORS[color] || '#6b7280'} />
                </div>
            </CardContent>
        </Card>
    );
}

interface MonitoringPanelProps {
    workflowId?: string;
}

export function MonitoringPanel({ workflowId }: MonitoringPanelProps) {
    const [timeRange, setTimeRange] = useState<TimeRange>('24h');
    const { data: metrics, isLoading, error } = useMonitoringMetrics(timeRange, workflowId);
    const { data: timeSeries } = useMonitoringTimeSeries(workflowId ?? null, timeRange);

    // Extract sparkline data from time-series, fallback to empty arrays
    const executionData = useMemo(
        () => timeSeries?.points.map((p) => p.count) ?? [],
        [timeSeries]
    );
    const successData = useMemo(
        () => timeSeries?.points.map((p) => p.successful) ?? [],
        [timeSeries]
    );
    const tokenData = useMemo(
        () => timeSeries?.points.map((p) => p.failed) ?? [],
        [timeSeries]
    );
    const durationData = useMemo(
        () => timeSeries?.points.map((p) => p.count) ?? [],
        [timeSeries]
    );

    const handleTimeRangeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        setTimeRange(e.target.value as TimeRange);
    };

    return (
        <div className="h-full flex flex-col bg-white dark:bg-zinc-950">
            <div className="px-4 py-2 border-b border-zinc-200 dark:border-zinc-800">
                <div className="flex items-center justify-between">
                    <div>
                        <h3 className="text-base font-semibold">Monitoring</h3>
                        <p className="text-sm text-muted-foreground">
                            Track your workflow performance and usage
                        </p>
                    </div>
                    <select
                        className="h-9 rounded-md border px-3 text-sm bg-background"
                        value={timeRange}
                        onChange={handleTimeRangeChange}
                        aria-label="Select time range"
                    >
                        <option value="1h">Last Hour</option>
                        <option value="24h">Last 24 Hours</option>
                        <option value="7d">Last 7 Days</option>
                        <option value="30d">Last 30 Days</option>
                    </select>
                </div>
            </div>

            <div className="p-4 flex-1 overflow-auto">
                {error ? (
                    <div className="text-center py-8 text-muted-foreground">
                        <p className="text-sm">Unable to load monitoring data</p>
                        <p className="text-xs mt-1">Please try again later</p>
                    </div>
                ) : (
                    <>
                        <div className="grid grid-cols-2 gap-4">
                            <MetricCard
                                title="Total Executions"
                                value={metrics?.total_executions?.toLocaleString() || '0'}
                                icon={ChatCircle}
                                color="text-blue-500"
                                data={executionData}
                                isLoading={isLoading}
                            />
                            <MetricCard
                                title="Success Rate"
                                value={metrics ? `${metrics.success_rate.toFixed(1)}%` : '0%'}
                                icon={Users}
                                color="text-green-500"
                                data={successData}
                                isLoading={isLoading}
                            />
                            <MetricCard
                                title="Failed"
                                value={metrics?.failed?.toLocaleString() || '0'}
                                icon={Coins}
                                color="text-red-500"
                                data={tokenData}
                                isLoading={isLoading}
                            />
                            <MetricCard
                                title="Avg. Duration"
                                value={metrics?.avg_duration_ms ? `${(metrics.avg_duration_ms / 1000).toFixed(1)}s` : 'N/A'}
                                icon={ChartLine}
                                color="text-orange-500"
                                data={durationData}
                                isLoading={isLoading}
                            />
                        </div>

                        <div className="mt-6">
                            <h3 className="text-sm font-medium mb-3">Execution Summary</h3>
                            {isLoading ? (
                                <div className="h-24 flex items-center justify-center">
                                    <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
                                </div>
                            ) : (
                                <div className="space-y-3">
                                    <div className="flex justify-between text-sm">
                                        <span className="text-muted-foreground">Successful</span>
                                        <span className="text-green-600 font-medium">
                                            {metrics?.successful || 0}
                                        </span>
                                    </div>
                                    <div className="flex justify-between text-sm">
                                        <span className="text-muted-foreground">Failed</span>
                                        <span className="text-red-600 font-medium">
                                            {metrics?.failed || 0}
                                        </span>
                                    </div>
                                    <div className="flex justify-between text-sm">
                                        <span className="text-muted-foreground">Running</span>
                                        <span className="text-blue-600 font-medium">
                                            {metrics?.running || 0}
                                        </span>
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="mt-6">
                            <h3 className="text-sm font-medium mb-3">Time Range</h3>
                            <p className="text-xs text-muted-foreground">
                                Showing data for: {timeRange === '1h' ? 'Last Hour' :
                                    timeRange === '24h' ? 'Last 24 Hours' :
                                        timeRange === '7d' ? 'Last 7 Days' : 'Last 30 Days'}
                            </p>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
