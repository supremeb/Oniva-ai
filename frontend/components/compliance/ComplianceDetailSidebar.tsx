'use client';

import { type FC } from 'react';
import { format } from 'date-fns';
import { parseTimestamp } from '@/lib/utils';
import {
  Clock,
  User,
  Shield,
  FileText,
  CheckCircle,
  XCircle,
  ArrowRight,
  Copy,
} from '@phosphor-icons/react';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { SignatureVerificationBadge } from './SignatureVerificationBadge';
import type { ComplianceAuditLog, ReviewTicket } from '@/lib/api/compliance';

type SelectedItem =
  | { type: 'audit-log'; data: ComplianceAuditLog }
  | { type: 'review-ticket'; data: ReviewTicket };

interface ComplianceDetailSidebarProps {
  item: SelectedItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAssign?: (ticketId: string) => void;
  onApprove?: (ticketId: string) => void;
  onReject?: (ticketId: string) => void;
  onViewTimeline?: (workflowId: string) => void;
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between py-2.5 border-b border-border/40 last:border-0">
      <span className="text-overline shrink-0 pt-0.5">
        {label}
      </span>
      <div className="text-sm text-foreground text-right ml-4">{children}</div>
    </div>
  );
}

function CopyableId({ id }: { id: string }) {
  return (
    <button
      onClick={() => navigator.clipboard.writeText(id)}
      className="
        inline-flex items-center gap-1 font-mono text-xs text-muted-foreground
        hover:text-foreground transition-colors
      "
      title="Copy full ID"
    >
      {id.slice(0, 12)}...
      <Copy className="h-3 w-3" />
    </button>
  );
}

const AuditLogDetail: FC<{
  log: ComplianceAuditLog;
  onViewTimeline?: (workflowId: string) => void;
}> = ({ log, onViewTimeline }) => (
  <div className="space-y-6">
    {/* Core Info */}
    <div>
      <DetailRow label="ID">
        <CopyableId id={log.id} />
      </DetailRow>
      <DetailRow label="Timestamp">
        {format(parseTimestamp(log.timestamp), 'MMM d, yyyy HH:mm:ss')}
      </DetailRow>
      <DetailRow label="Agent">
        <span className="font-mono text-xs">{log.agent_id}</span>
      </DetailRow>
      <DetailRow label="State">
        <span className="px-2 py-0.5 text-xs rounded-md border bg-muted/60 text-muted-foreground border-border/60">
          {log.workflow_state.replace(/_/g, ' ')}
        </span>
      </DetailRow>
      <DetailRow label="Signature">
        <SignatureVerificationBadge
          status={log.signature ? 'valid' : 'none'}
          showLabel
        />
      </DetailRow>
    </div>

    {/* Decision */}
    <div>
      <h4 className="text-overline mb-2">Decision</h4>
      <p className="text-sm text-foreground font-medium">{log.decision}</p>
    </div>

    {/* Rationale */}
    {log.rationale && (
      <div>
        <h4 className="text-overline mb-2">Rationale</h4>
        <p className="text-body-muted">{log.rationale}</p>
      </div>
    )}

    {/* Data Sources */}
    {log.data_sources && log.data_sources.length > 0 && (
      <div>
        <h4 className="text-overline mb-2">
          Data Sources
        </h4>
        <div className="space-y-1">
          {log.data_sources.map((source, i) => (
            <div
              key={i}
              className="px-3 py-1.5 bg-muted/50 rounded-lg text-mono"
            >
              {source}
            </div>
          ))}
        </div>
      </div>
    )}

    {/* Workflow Info */}
    {log.workflow_id && (
      <div>
        <DetailRow label="Workflow">
          <CopyableId id={log.workflow_id} />
        </DetailRow>
        {log.session_id && (
          <DetailRow label="Session">
            <CopyableId id={log.session_id} />
          </DetailRow>
        )}
      </div>
    )}

    {/* Actions */}
    {log.workflow_id && onViewTimeline && (
      <div className="pt-2">
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={() => onViewTimeline(log.workflow_id!)}
        >
          View Workflow Timeline
          <ArrowRight className="h-4 w-4 ml-2" />
        </Button>
      </div>
    )}
  </div>
);

const ReviewTicketDetail: FC<{
  ticket: ReviewTicket;
  onAssign?: (ticketId: string) => void;
  onApprove?: (ticketId: string) => void;
  onReject?: (ticketId: string) => void;
  onViewTimeline?: (workflowId: string) => void;
}> = ({ ticket, onAssign, onApprove, onReject, onViewTimeline }) => (
  <div className="space-y-6">
    {/* Core Info */}
    <div>
      <DetailRow label="ID">
        <CopyableId id={ticket.id} />
      </DetailRow>
      <DetailRow label="Status">
        <span className="capitalize">{ticket.status.replace(/_/g, ' ')}</span>
      </DetailRow>
      <DetailRow label="Priority">
        <span className={
          ticket.priority.toUpperCase() === 'URGENT' || ticket.priority.toUpperCase() === 'HIGH'
            ? 'font-semibold'
            : ''
        }>
          {ticket.priority.toUpperCase()}
        </span>
      </DetailRow>
      <DetailRow label="Risk">
        <span className="uppercase">{ticket.risk_level}</span>
      </DetailRow>
      <DetailRow label="Type">
        {ticket.review_type.replace(/_/g, ' ')}
      </DetailRow>
      <DetailRow label="Agent">
        <span className="font-mono text-xs">{ticket.agent_id}</span>
      </DetailRow>
      <DetailRow label="Created">
        {format(parseTimestamp(ticket.created_at), 'MMM d, yyyy HH:mm')}
      </DetailRow>
      {ticket.assigned_to && (
        <DetailRow label="Assigned">
          <div className="flex items-center gap-1">
            <User className="h-3 w-3" />
            {ticket.assigned_to}
          </div>
        </DetailRow>
      )}
      {ticket.due_at && (
        <DetailRow label="Due">
          {format(parseTimestamp(ticket.due_at), 'MMM d, yyyy HH:mm')}
        </DetailRow>
      )}
      {ticket.sla_breached && (
        <DetailRow label="SLA">
          <span className="font-medium">Breached</span>
        </DetailRow>
      )}
    </div>

    {/* Description */}
    <div>
      <h4 className="text-overline mb-2">Description</h4>
      <p className="text-body-muted">{ticket.description}</p>
    </div>

    {/* Review Data */}
    {ticket.review_data && Object.keys(ticket.review_data).length > 0 && (
      <div>
        <h4 className="text-overline mb-2">
          Review Data
        </h4>
        <div className="bg-muted/50 rounded-lg p-3 overflow-x-auto">
          <pre className="text-xs text-muted-foreground font-mono whitespace-pre-wrap">
            {JSON.stringify(ticket.review_data, null, 2)}
          </pre>
        </div>
      </div>
    )}

    {/* Decision Info */}
    {ticket.decision && (
      <div>
        <h4 className="text-overline mb-2">Resolution</h4>
        <div className="flex items-start gap-2 p-3 bg-muted/30 rounded-lg">
          {ticket.decision === 'approved' ? (
            <CheckCircle weight="fill" className="h-4 w-4 text-foreground mt-0.5 shrink-0" />
          ) : (
            <XCircle weight="fill" className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
          )}
          <div className="space-y-1">
            <p className="text-xs font-medium text-foreground">
              {ticket.decision.toUpperCase()}
              {ticket.reviewed_by ? ` by ${ticket.reviewed_by}` : ''}
            </p>
            {ticket.review_notes && (
              <p className="text-xs text-muted-foreground">{ticket.review_notes}</p>
            )}
            {ticket.reviewed_at && (
              <p className="text-xs text-muted-foreground/70">
                {format(parseTimestamp(ticket.reviewed_at), 'MMM d, yyyy HH:mm')}
              </p>
            )}
          </div>
        </div>
      </div>
    )}

    {/* Workflow Info */}
    {ticket.workflow_id && (
      <div>
        <DetailRow label="Workflow">
          <CopyableId id={ticket.workflow_id} />
        </DetailRow>
        {ticket.session_id && (
          <DetailRow label="Session">
            <CopyableId id={ticket.session_id} />
          </DetailRow>
        )}
      </div>
    )}

    {/* Actions */}
    <div className="space-y-2 pt-2">
      {!['approved', 'rejected'].includes(ticket.status.toLowerCase()) && (
        <>
          {onAssign && (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => onAssign(ticket.id)}
            >
              <User className="h-4 w-4 mr-2" />
              Assign Reviewer
            </Button>
          )}
          <div className="flex gap-2">
            {onApprove && (
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => onApprove(ticket.id)}
              >
                <CheckCircle className="h-4 w-4 mr-2" />
                Approve
              </Button>
            )}
            {onReject && (
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => onReject(ticket.id)}
              >
                <XCircle className="h-4 w-4 mr-2" />
                Reject
              </Button>
            )}
          </div>
        </>
      )}
      {ticket.workflow_id && onViewTimeline && (
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={() => onViewTimeline(ticket.workflow_id!)}
        >
          View Workflow Timeline
          <ArrowRight className="h-4 w-4 ml-2" />
        </Button>
      )}
    </div>
  </div>
);

export const ComplianceDetailSidebar: FC<ComplianceDetailSidebarProps> = ({
  item,
  open,
  onOpenChange,
  onAssign,
  onApprove,
  onReject,
  onViewTimeline,
}) => {
  const title = item?.type === 'audit-log'
    ? item.data.decision
    : item?.type === 'review-ticket'
      ? item.data.title
      : '';

  const description = item?.type === 'audit-log'
    ? 'Audit Log Entry'
    : item?.type === 'review-ticket'
      ? 'Review Ticket'
      : '';

  const icon = item?.type === 'audit-log'
    ? <FileText weight="duotone" className="h-5 w-5 text-muted-foreground shrink-0" />
    : <Shield weight="duotone" className="h-5 w-5 text-muted-foreground shrink-0" />;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-lg overflow-y-auto">
        <SheetHeader className="pb-4 border-b border-border/40">
          <div className="flex items-center gap-2">
            {icon}
            <div>
              <SheetTitle className="text-base">{title}</SheetTitle>
              <SheetDescription>{description}</SheetDescription>
            </div>
          </div>
        </SheetHeader>
        <div className="pt-6">
          {item?.type === 'audit-log' && (
            <AuditLogDetail
              log={item.data}
              onViewTimeline={onViewTimeline}
            />
          )}
          {item?.type === 'review-ticket' && (
            <ReviewTicketDetail
              ticket={item.data}
              onAssign={onAssign}
              onApprove={onApprove}
              onReject={onReject}
              onViewTimeline={onViewTimeline}
            />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
};
