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
import { CircleNotch, Warning, Bell, ArrowLeft } from '@phosphor-icons/react';
import { StatusBadge } from '@/components/chat/shared/data/StatusBadge';
import { useAlertHistory } from '@/hooks/useObservability';
import { format } from 'date-fns';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import type { StatusType } from '@/components/chat/shared/data/StatusBadge';

function notificationStatusToBadge(status: string): StatusType {
  switch (status) {
    case 'delivered': return 'success';
    case 'failed': return 'error';
    case 'pending': return 'pending';
    default: return 'neutral';
  }
}

function triggerTypeLabel(type: string): string {
  switch (type) {
    case 'execution_failed': return 'Execution Failed';
    case 'high_failure_rate': return 'High Failure Rate';
    case 'latency_threshold': return 'Latency Threshold';
    case 'circuit_breaker_open': return 'Circuit Breaker Open';
    default: return type;
  }
}

export default function AlertHistoryPage() {
  const { data: alerts, isLoading, error } = useAlertHistory(50);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link href="/observability">
              <Button variant="ghost" size="sm" className="gap-1 h-7 px-2">
                <ArrowLeft className="h-4 w-4" />
                Observability
              </Button>
            </Link>
          </div>
          <h1 className="text-page-title flex items-center gap-2">
            <Bell className="h-6 w-6" />
            Alert History
          </h1>
          <p className="text-page-description mt-1">
            Recent alert deliveries and notification status
          </p>
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="text-center py-12 text-muted-foreground">
          <Warning className="h-8 w-8 mx-auto mb-2" />
          <p className="text-sm">Failed to load alert history</p>
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div className="flex justify-center py-12">
          <CircleNotch className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Alert History Table */}
      {!isLoading && !error && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Alerts</CardTitle>
            <CardDescription>
              {alerts?.length ?? 0} alert{(alerts?.length ?? 0) !== 1 ? 's' : ''} delivered
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!alerts || alerts.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <Bell className="h-8 w-8 mx-auto mb-2" />
                <p className="text-sm">No alerts yet</p>
                <p className="text-xs mt-1">
                  Configure alert rules in Settings to start receiving notifications
                </p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Trigger</TableHead>
                    <TableHead>Message</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {alerts.map((alert) => (
                    <TableRow key={alert.id}>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {triggerTypeLabel(alert.trigger_type)}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-md">
                        <p className="text-sm truncate">{alert.message}</p>
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          status={notificationStatusToBadge(alert.notification_status)}
                          label={alert.notification_status}
                          size="xs"
                        />
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                        {format(new Date(alert.created_at), 'MMM d, HH:mm')}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
