'use client';

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
import { CircleNotch, Warning, Robot } from '@phosphor-icons/react';
import { StatusBadge } from '@/components/chat/shared/data/StatusBadge';
import { useAllAgentsHealth } from '@/hooks/useObservability';
import { cn } from '@/lib/utils';
import { formatDistanceToNow } from 'date-fns';

function accuracyColor(accuracy: number | null): string {
  if (accuracy === null) return 'text-muted-foreground';
  if (accuracy >= 0.9) return 'text-green-600';
  if (accuracy >= 0.7) return 'text-amber-600';
  return 'text-red-600';
}

function circuitBreakerToStatus(status: string): 'success' | 'warning' | 'neutral' {
  if (status === 'healthy') return 'success';
  if (status === 'degraded') return 'warning';
  return 'neutral';
}

export function AgentHealthTab() {
  const { data, isLoading, error } = useAllAgentsHealth();

  if (error) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <Warning className="h-8 w-8 mx-auto mb-2" />
        <p className="text-sm">Failed to load agent health data</p>
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

  if (!data || data.agents.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <Robot className="h-8 w-8 mx-auto mb-2" />
        <p className="text-sm">No agents found</p>
        <p className="text-xs mt-1">Create agents to see their health status here</p>
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Agent Health Overview</CardTitle>
        <CardDescription>
          Circuit breaker status, accuracy, and drift detection for {data.total} agents
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Agent</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Circuit Breaker</TableHead>
              <TableHead className="text-right">Accuracy</TableHead>
              <TableHead>Drift</TableHead>
              <TableHead>Last Evaluated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.agents.map((agent) => (
              <TableRow key={agent.agent_id}>
                <TableCell className="font-medium">{agent.agent_name}</TableCell>
                <TableCell>
                  <Badge variant="outline" className="text-xs">
                    {agent.agent_type}
                  </Badge>
                </TableCell>
                <TableCell>
                  <StatusBadge
                    status={circuitBreakerToStatus(agent.circuit_breaker_status)}
                    label={agent.circuit_breaker_status}
                    size="xs"
                  />
                </TableCell>
                <TableCell className={cn('text-right font-mono', accuracyColor(agent.accuracy))}>
                  {agent.accuracy !== null
                    ? `${(agent.accuracy * 100).toFixed(1)}%`
                    : '--'}
                </TableCell>
                <TableCell>
                  {agent.has_drift === true && (
                    <StatusBadge status="warning" label="Drift detected" size="xs" />
                  )}
                  {agent.has_drift === false && (
                    <StatusBadge status="success" label="Stable" size="xs" />
                  )}
                  {agent.has_drift === null && (
                    <span className="text-xs text-muted-foreground">--</span>
                  )}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {agent.last_evaluation_at
                    ? formatDistanceToNow(new Date(agent.last_evaluation_at), { addSuffix: true })
                    : '--'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
