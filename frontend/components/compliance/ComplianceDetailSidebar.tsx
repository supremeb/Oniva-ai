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
import type { ComplianceAuditLog } from '@/lib/api/compliance';

type SelectedItem = { type: 'audit-log'; data: ComplianceAuditLog };

interface ComplianceDetailSidebarProps {
  item: SelectedItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
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

export const ComplianceDetailSidebar: FC<ComplianceDetailSidebarProps> = ({
  item,
  open,
  onOpenChange,
  onViewTimeline,
}) => {
  const title = item?.data.decision ?? '';
  const description = item ? 'Audit Log Entry' : '';
  const icon = <FileText weight="duotone" className="h-5 w-5 text-muted-foreground shrink-0" />;

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
        </div>
      </SheetContent>
    </Sheet>
  );
};
