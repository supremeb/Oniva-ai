'use client';

import { memo, useRef, useCallback, forwardRef, useImperativeHandle } from 'react';
import {
  LineChart,
  BarChart,
  PieChart,
  AreaChart,
  Line,
  Bar,
  Pie,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { exportChartPNG, exportChartSVG, exportChartCSV } from './chart-export';

// ── Chart config types ─────────────────────────────────────
export interface ChartSeries {
  dataKey: string;
  name?: string;
  color?: string;
}

export interface ChartConfig {
  type: 'line' | 'bar' | 'pie' | 'area';
  data: Array<Record<string, string | number>>;
  series: ChartSeries[];
  xAxisKey?: string;
  title?: string;
}

export interface InteractiveChartHandle {
  exportPNG: (filename?: string) => Promise<void>;
  exportSVG: (filename?: string) => void;
  exportCSV: (filename?: string) => void;
}

interface InteractiveChartProps {
  config: ChartConfig;
  width?: number;
  height?: number;
}

// Default color palette
const COLORS = [
  '#6366f1', '#06b6d4', '#f59e0b', '#10b981',
  '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6',
];

function getColor(index: number, explicit?: string): string {
  return explicit || COLORS[index % COLORS.length];
}

export const InteractiveChart = memo(
  forwardRef<InteractiveChartHandle, InteractiveChartProps>(function InteractiveChart(
    { config, height = 300 },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null);

    const getSvgElement = useCallback((): SVGSVGElement | null => {
      return containerRef.current?.querySelector('svg') ?? null;
    }, []);

    useImperativeHandle(ref, () => ({
      exportPNG: async (filename) => {
        const svg = getSvgElement();
        if (svg) await exportChartPNG(svg, filename || `${config.title || 'chart'}.png`);
      },
      exportSVG: (filename) => {
        const svg = getSvgElement();
        if (svg) exportChartSVG(svg, filename || `${config.title || 'chart'}.svg`);
      },
      exportCSV: (filename) => {
        exportChartCSV(config.data, filename || `${config.title || 'chart'}.csv`);
      },
    }), [config, getSvgElement]);

    const { type, data, series, xAxisKey } = config;
    const xKey = xAxisKey || (data.length > 0 ? Object.keys(data[0])[0] : 'name');

    return (
      <div ref={containerRef} className="w-full">
        <ResponsiveContainer width="100%" height={height}>
          {type === 'line' ? (
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey={xKey} tick={{ fontSize: 12 }} stroke="#9ca3af" />
              <YAxis tick={{ fontSize: 12 }} stroke="#9ca3af" />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: '1px solid #e5e7eb',
                  boxShadow: '0 4px 6px -1px rgba(0,0,0,.1)',
                  fontSize: 13,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 13 }} />
              {series.map((s, i) => (
                <Line
                  key={s.dataKey}
                  type="monotone"
                  dataKey={s.dataKey}
                  name={s.name || s.dataKey}
                  stroke={getColor(i, s.color)}
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  activeDot={{ r: 5 }}
                />
              ))}
            </LineChart>
          ) : type === 'bar' ? (
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey={xKey} tick={{ fontSize: 12 }} stroke="#9ca3af" />
              <YAxis tick={{ fontSize: 12 }} stroke="#9ca3af" />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: '1px solid #e5e7eb',
                  boxShadow: '0 4px 6px -1px rgba(0,0,0,.1)',
                  fontSize: 13,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 13 }} />
              {series.map((s, i) => (
                <Bar
                  key={s.dataKey}
                  dataKey={s.dataKey}
                  name={s.name || s.dataKey}
                  fill={getColor(i, s.color)}
                  radius={[4, 4, 0, 0]}
                />
              ))}
            </BarChart>
          ) : type === 'area' ? (
            <AreaChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey={xKey} tick={{ fontSize: 12 }} stroke="#9ca3af" />
              <YAxis tick={{ fontSize: 12 }} stroke="#9ca3af" />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: '1px solid #e5e7eb',
                  boxShadow: '0 4px 6px -1px rgba(0,0,0,.1)',
                  fontSize: 13,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 13 }} />
              {series.map((s, i) => (
                <Area
                  key={s.dataKey}
                  type="monotone"
                  dataKey={s.dataKey}
                  name={s.name || s.dataKey}
                  stroke={getColor(i, s.color)}
                  fill={getColor(i, s.color)}
                  fillOpacity={0.15}
                  strokeWidth={2}
                />
              ))}
            </AreaChart>
          ) : (
            // Pie chart
            <PieChart>
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: '1px solid #e5e7eb',
                  boxShadow: '0 4px 6px -1px rgba(0,0,0,.1)',
                  fontSize: 13,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 13 }} />
              <Pie
                data={data}
                dataKey={series[0]?.dataKey || 'value'}
                nameKey={xKey}
                cx="50%"
                cy="50%"
                outerRadius={Math.min(height * 0.35, 120)}
                label={(props) => {
                  const { name, percent } = props as { name: string; percent: number };
                  return `${name}: ${((percent ?? 0) * 100).toFixed(0)}%`;
                }}
                labelLine={{ stroke: '#9ca3af' }}
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={getColor(i)} />
                ))}
              </Pie>
            </PieChart>
          )}
        </ResponsiveContainer>
      </div>
    );
  }),
);
