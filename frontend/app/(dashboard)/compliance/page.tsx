'use client';

import { useState, useCallback } from 'react';
import {
  Shield,
  ShieldCheck,
  Clock,
  Download,
  CheckCircle,
  XCircle,
  ChartLine,
  FileText,
} from '@phosphor-icons/react';
import { DataState } from '@/components/ui/data-state';
import { useAuthStore } from '@/lib/stores/auth';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { AuditLogTable } from '@/components/compliance/AuditLogTable';
import { ReviewTicketTable } from '@/components/compliance/ReviewTicketTable';
import { ComplianceDetailSidebar } from '@/components/compliance/ComplianceDetailSidebar';
import { WorkflowStateTimeline } from '@/components/compliance/WorkflowStateTimeline';
import { SignatureVerificationBadge } from '@/components/compliance/SignatureVerificationBadge';
import {
  useComplianceStats,
  useAuditLogs,
  useReviewTickets,
  useWorkflowReplay,
  useExportAuditLogs,
  useAssignReviewer,
  useApproveReview,
  useRejectReview,
} from '@/hooks/useCompliance';
import type { ComplianceAuditLog, ReviewTicket } from '@/lib/api/compliance';

export default function CompliancePage() {
  const user = useAuthStore((s) => s.user);
  const [selectedItem, setSelectedItem] = useState<
    | { type: 'audit-log'; data: ComplianceAuditLog }
    | { type: 'review-ticket'; data: ReviewTicket }
    | null
  >(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string | null>(null);
  const [showTimeline, setShowTimeline] = useState(false);
  const [confirmAction, setConfirmAction] = useState<{
    type: 'approve' | 'reject';
    ticketId: string;
    title: string;
  } | null>(null);
  const [auditOffset, setAuditOffset] = useState(0);
  const AUDIT_PAGE_SIZE = 50;

  // Queries
  const { data: stats, isLoading: statsLoading, isError: statsError, refetch: refetchStats } = useComplianceStats();
  const { data: auditLogs = [], isLoading: logsLoading, isError: logsError, refetch: refetchLogs } = useAuditLogs({ limit: AUDIT_PAGE_SIZE, offset: auditOffset });
  const { data: reviewTickets = [], isLoading: ticketsLoading, isError: ticketsError, refetch: refetchTickets } = useReviewTickets({ limit: 50 });
  const { data: workflowReplay } = useWorkflowReplay(showTimeline ? selectedWorkflow : null);

  // Mutations
  const exportMutation = useExportAuditLogs();
  const assignMutation = useAssignReviewer();
  const approveMutation = useApproveReview();
  const rejectMutation = useRejectReview();

  const handleViewLogDetails = useCallback((log: ComplianceAuditLog) => {
    setSelectedItem({ type: 'audit-log', data: log });
    setSidebarOpen(true);
  }, []);

  const handleViewTicketDetails = useCallback((ticket: ReviewTicket) => {
    setSelectedItem({ type: 'review-ticket', data: ticket });
    setSidebarOpen(true);
  }, []);

  const handleViewTimeline = useCallback((workflowId: string) => {
    setSidebarOpen(false);
    setSelectedWorkflow(workflowId);
    setShowTimeline(true);
  }, []);

  const handleConfirmAction = useCallback(() => {
    if (!confirmAction) return;
    if (confirmAction.type === 'approve') {
      approveMutation.mutate({ ticketId: confirmAction.ticketId, reason: 'Approved after review' });
    } else {
      rejectMutation.mutate({ ticketId: confirmAction.ticketId, reason: 'Rejected due to policy violation' });
    }
    setConfirmAction(null);
  }, [confirmAction, approveMutation, rejectMutation]);

  return (
    <div className="p-6 lg:p-8 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-page-title">Compliance & Audit</h1>
          <p className="text-page-description mt-1">
            Regulatory oversight, audit trails, and review management
          </p>
        </div>
      </div>

      {/* Stats Cards */}
      <DataState
        isLoading={statsLoading}
        isError={statsError}
        onRetry={() => refetchStats()}
        loadingText="Loading compliance stats..."
      >
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          {/* Total Audit Logs */}
          <div className="bg-card border border-border/60 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <FileText weight="duotone" className="h-5 w-5 text-muted-foreground" />
              <span className="text-xs text-muted-foreground">Last 30d</span>
            </div>
            <div className="space-y-1">
              <p className="text-stat">
                {stats?.total_audit_logs.toLocaleString()}
              </p>
              <p className="text-stat-label">
                Audit Entries
              </p>
            </div>
            {stats && (
              <div className="mt-3 pt-3 border-t border-border/40">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">24h:</span>
                  <span className="text-foreground font-medium">
                    +{stats.recent_activity.last_24h}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Pending Reviews */}
          <div className="bg-card border border-border/60 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <Clock weight="duotone" className="h-5 w-5 text-muted-foreground" />
              <span className="text-xs text-muted-foreground font-medium">
                Requires Action
              </span>
            </div>
            <div className="space-y-1">
              <p className="text-stat">
                {stats?.pending_reviews}
              </p>
              <p className="text-stat-label">
                Pending Reviews
              </p>
            </div>
            {reviewTickets.length > 0 && (
              <div className="mt-3 pt-3 border-t border-border/40">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Urgent:</span>
                  <span className="text-foreground font-medium">
                    {reviewTickets.filter((t) => t.priority.toLowerCase() === 'urgent').length}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Chain Integrity */}
          <div className="bg-card border border-border/60 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <ShieldCheck weight="duotone" className="h-5 w-5 text-muted-foreground" />
              <SignatureVerificationBadge
                status={stats?.chain_integrity.status === 'VALID' ? 'valid' : 'invalid'}
                showLabel={false}
              />
            </div>
            <div className="space-y-1">
              <p className="text-stat">
                {stats?.chain_integrity.status}
              </p>
              <p className="text-stat-label">
                Chain Status
              </p>
            </div>
            {stats?.chain_integrity && (
              <div className="mt-3 pt-3 border-t border-border/40">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Verified:</span>
                  <span className="text-foreground">
                    {new Date(stats.chain_integrity.last_verified).toLocaleTimeString()}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Risk Distribution */}
          <div className="bg-card border border-border/60 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <ChartLine weight="duotone" className="h-5 w-5 text-muted-foreground" />
              <span className="text-xs text-muted-foreground">Distribution</span>
            </div>
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2">
                Risk Levels
              </p>
              {stats && (
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-foreground font-medium">Critical</span>
                    <span className="font-semibold text-foreground">{stats.risk_distribution.critical}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-foreground">High</span>
                    <span className="font-medium text-foreground">{stats.risk_distribution.high}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Medium</span>
                    <span className="font-medium text-foreground">{stats.risk_distribution.medium}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Low</span>
                    <span className="font-medium text-foreground">{stats.risk_distribution.low}</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </DataState>

      {/* Tabbed Content */}
      <Tabs defaultValue="review-queue">
        <div className="flex items-center justify-between mb-4">
          <TabsList>
            <TabsTrigger value="review-queue" className="gap-2">
              Review Queue
              <span className="px-1.5 py-0.5 text-[10px] bg-muted text-muted-foreground rounded-full leading-none">
                {reviewTickets.length}
              </span>
            </TabsTrigger>
            <TabsTrigger value="audit-activity" className="gap-2">
              Audit Activity
              <span className="px-1.5 py-0.5 text-[10px] bg-muted text-muted-foreground rounded-full leading-none">
                {auditLogs.length}
              </span>
            </TabsTrigger>
          </TabsList>

          <button
            onClick={() => exportMutation.mutate({ format: 'csv' })}
            disabled={exportMutation.isPending}
            className="
              px-3 py-1.5
              bg-muted border border-border
              rounded-lg text-sm text-muted-foreground
              hover:bg-accent hover:text-foreground transition-colors
              flex items-center gap-1.5
              disabled:opacity-50 disabled:cursor-not-allowed
            "
          >
            <Download className="h-4 w-4" />
            Export CSV
          </button>
        </div>

        {/* Review Queue Tab */}
        <TabsContent value="review-queue">
          <div className="bg-card border border-border/60 rounded-xl p-6">
            <DataState
              isLoading={ticketsLoading}
              isError={ticketsError}
              isEmpty={reviewTickets.length === 0}
              onRetry={() => refetchTickets()}
              emptyText="No pending reviews"
              emptyIcon={<CheckCircle className="h-12 w-12" />}
            >
              <ReviewTicketTable
                tickets={reviewTickets}
                isLoading={ticketsLoading}
                onAssign={(ticketId) =>
                  assignMutation.mutate({ ticketId, reviewerId: user?.id || '' })
                }
                onApprove={(ticketId) => {
                  const ticket = reviewTickets.find((t) => t.id === ticketId);
                  if (ticket) setConfirmAction({ type: 'approve', ticketId, title: ticket.title });
                }}
                onReject={(ticketId) => {
                  const ticket = reviewTickets.find((t) => t.id === ticketId);
                  if (ticket) setConfirmAction({ type: 'reject', ticketId, title: ticket.title });
                }}
                onViewDetails={handleViewTicketDetails}
              />
            </DataState>
          </div>
        </TabsContent>

        {/* Audit Activity Tab */}
        <TabsContent value="audit-activity">
          <div className="bg-card border border-border/60 rounded-xl p-6">
            <DataState
              isLoading={logsLoading}
              isError={logsError}
              isEmpty={auditLogs.length === 0}
              onRetry={() => refetchLogs()}
              emptyText="No audit logs recorded yet"
              emptyIcon={<Shield className="h-12 w-12" />}
            >
              <AuditLogTable
                logs={auditLogs}
                isLoading={logsLoading}
                onViewDetails={handleViewLogDetails}
                onExport={() => exportMutation.mutate({ format: 'csv' })}
              />
            </DataState>

            {/* Pagination */}
            {auditLogs.length >= AUDIT_PAGE_SIZE && (
              <div className="flex items-center justify-between mt-4 pt-4 border-t border-border/40">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={auditOffset === 0}
                  onClick={() => setAuditOffset(Math.max(0, auditOffset - AUDIT_PAGE_SIZE))}
                >
                  Previous
                </Button>
                <span className="text-xs text-muted-foreground">
                  Showing {auditOffset + 1}–{auditOffset + auditLogs.length}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setAuditOffset(auditOffset + AUDIT_PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>

      {/* Confirmation Dialog */}
      <Dialog open={!!confirmAction} onOpenChange={() => setConfirmAction(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {confirmAction?.type === 'approve' ? 'Approve Review' : 'Reject Review'}
            </DialogTitle>
            <DialogDescription>
              {confirmAction?.type === 'approve'
                ? `Are you sure you want to approve "${confirmAction?.title}"? This action will be recorded in the audit trail.`
                : `Are you sure you want to reject "${confirmAction?.title}"? This action will be recorded in the audit trail.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant={confirmAction?.type === 'approve' ? 'default' : 'destructive'}
              onClick={handleConfirmAction}
              disabled={approveMutation.isPending || rejectMutation.isPending}
            >
              {confirmAction?.type === 'approve' ? 'Approve' : 'Reject'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Detail Sidebar */}
      <ComplianceDetailSidebar
        item={selectedItem}
        open={sidebarOpen}
        onOpenChange={setSidebarOpen}
        onAssign={(ticketId) =>
          assignMutation.mutate({ ticketId, reviewerId: user?.id || '' })
        }
        onApprove={(ticketId) => {
          const ticket = reviewTickets.find((t) => t.id === ticketId);
          if (ticket) {
            setSidebarOpen(false);
            setConfirmAction({ type: 'approve', ticketId, title: ticket.title });
          }
        }}
        onReject={(ticketId) => {
          const ticket = reviewTickets.find((t) => t.id === ticketId);
          if (ticket) {
            setSidebarOpen(false);
            setConfirmAction({ type: 'reject', ticketId, title: ticket.title });
          }
        }}
        onViewTimeline={handleViewTimeline}
      />

      {/* Workflow Timeline Dialog */}
      <Dialog open={showTimeline && !!selectedWorkflow} onOpenChange={(open) => {
        if (!open) {
          setShowTimeline(false);
          setSelectedWorkflow(null);
        }
      }}>
        <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Workflow Execution Timeline</DialogTitle>
            <DialogDescription>
              Step-by-step replay of workflow {selectedWorkflow}
            </DialogDescription>
          </DialogHeader>
          {workflowReplay && (
            <WorkflowStateTimeline replay={workflowReplay} />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
