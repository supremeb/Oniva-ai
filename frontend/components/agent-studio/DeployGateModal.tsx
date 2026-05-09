'use client';

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  CheckCircle,
  XCircle,
  Warning,
  Rocket,
  CircleNotch,
  ShieldCheck,
} from '@phosphor-icons/react';
import { cn, formatAccuracy } from '@/lib/utils';
import { DeltaIndicator } from '@/components/ui/delta-indicator';
import {
  getDeployGateStatus,
  deployVersion,
  type DeployGateStatus,
  type DeployResult,
} from '@/lib/api/agent-studio';
import { toast } from 'sonner';

interface DeployGateModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentId: string;
  versionId: string;
  versionNumber: string;
  onDeploySuccess?: (result: DeployResult) => void;
}

export function DeployGateModal({
  open,
  onOpenChange,
  agentId,
  versionId,
  versionNumber,
  onDeploySuccess,
}: DeployGateModalProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [isDeploying, setIsDeploying] = useState(false);
  const [gateStatus, setGateStatus] = useState<DeployGateStatus | null>(null);
  const [forceDeployReason, setForceDeployReason] = useState('');
  const [confirmForce, setConfirmForce] = useState(false);
  const [notes, setNotes] = useState('');

  // Load gate status
  useEffect(() => {
    if (!open) return;

    const loadGateStatus = async () => {
      try {
        setIsLoading(true);
        const status = await getDeployGateStatus(agentId, versionId);
        setGateStatus(status);
      } catch (e) {
        console.error('Failed to load gate status:', e);
        toast.error('Error', { description: 'Failed to load deployment gate status' });
      } finally {
        setIsLoading(false);
      }
    };

    loadGateStatus();
  }, [open, agentId, versionId]);

  // Reset state when modal closes
  useEffect(() => {
    if (!open) {
      setForceDeployReason('');
      setConfirmForce(false);
      setNotes('');
    }
  }, [open]);

  // Handle deploy
  const handleDeploy = async (force: boolean = false) => {
    try {
      setIsDeploying(true);

      const result = await deployVersion(agentId, versionId, {
        force,
        force_reason: force ? forceDeployReason : undefined,
        notes: notes || undefined,
      });

      if (result.success) {
        toast('Deployment Successful', { description: `Version ${result.version} is now live` });
        onDeploySuccess?.(result);
        onOpenChange(false);
      } else {
        toast.error('Deployment Blocked', { description: result.block_reason || 'Gate checks failed' });
      }
    } catch (e) {
      toast.error('Deployment Failed', { description: 'An error occurred during deployment' });
    } finally {
      setIsDeploying(false);
    }
  };

  const canForceDeploy = !gateStatus?.can_deploy && forceDeployReason.trim() && confirmForce;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-primary" weight="fill" />
            <DialogTitle>Deploy Version {versionNumber}</DialogTitle>
          </div>
          <DialogDescription>
            Review deployment gates before going live
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <CircleNotch className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : gateStatus ? (
          <div className="space-y-4 py-4">
            {/* Status Banner */}
            <div
              className={cn(
                'flex items-center gap-3 p-4 rounded-lg',
                gateStatus.can_deploy
                  ? 'bg-green-50 border border-green-200'
                  : 'bg-amber-50 border border-amber-200'
              )}
            >
              {gateStatus.can_deploy ? (
                <>
                  <CheckCircle
                    className="h-8 w-8 text-green-600 flex-shrink-0"
                    weight="fill"
                  />
                  <div>
                    <p className="font-medium text-green-800">
                      All Gates Passed
                    </p>
                    <p className="text-sm text-green-700">
                      This version is ready for deployment
                    </p>
                  </div>
                </>
              ) : (
                <>
                  <Warning
                    className="h-8 w-8 text-amber-600 flex-shrink-0"
                    weight="fill"
                  />
                  <div>
                    <p className="font-medium text-amber-800">
                      Gates Failed
                    </p>
                    <p className="text-sm text-amber-700">
                      Some requirements were not met
                    </p>
                  </div>
                </>
              )}
            </div>

            {/* Gate Checks */}
            <div className="space-y-2">
              <Label className="text-sm font-medium">Gate Checks</Label>
              <div className="space-y-2 rounded-lg border p-3">
                {gateStatus.checks.map((check, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between py-2 border-b last:border-0"
                  >
                    <div className="flex items-center gap-2">
                      {check.passed ? (
                        <CheckCircle
                          className="h-5 w-5 text-green-600"
                          weight="fill"
                        />
                      ) : (
                        <XCircle
                          className="h-5 w-5 text-red-600"
                          weight="fill"
                        />
                      )}
                      <div>
                        <p className="text-sm font-medium">{check.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {check.message}
                        </p>
                      </div>
                    </div>
                    <div className="text-right">
                      <p
                        className={cn(
                          'text-sm font-medium',
                          check.passed ? 'text-green-600' : 'text-red-600'
                        )}
                      >
                        {check.value}
                      </p>
                      {check.threshold && (
                        <p className="text-xs text-muted-foreground">
                          Threshold: {check.threshold}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Accuracy Comparison */}
            {(gateStatus.current_accuracy !== null ||
              gateStatus.new_accuracy !== null) && (
              <div className="space-y-2">
                <Label className="text-sm font-medium">Accuracy Comparison</Label>
                <div className="grid grid-cols-3 gap-3 rounded-lg border p-3">
                  <div className="text-center">
                    <p className="text-xs text-muted-foreground">Current</p>
                    <p className="text-lg font-bold">
                      {formatAccuracy(gateStatus.current_accuracy)}
                    </p>
                    {gateStatus.current_version && (
                      <p className="text-xs text-muted-foreground">
                        v{gateStatus.current_version}
                      </p>
                    )}
                  </div>
                  <div className="text-center flex flex-col items-center justify-center">
                    <DeltaIndicator delta={gateStatus.accuracy_delta} />
                  </div>
                  <div className="text-center">
                    <p className="text-xs text-muted-foreground">New</p>
                    <p className="text-lg font-bold">
                      {formatAccuracy(gateStatus.new_accuracy)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      v{gateStatus.new_version}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Deploy Notes */}
            {gateStatus.can_deploy && (
              <div className="space-y-2">
                <Label htmlFor="notes">Deploy Notes (optional)</Label>
                <Textarea
                  id="notes"
                  placeholder="Add notes about this deployment..."
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  rows={2}
                />
              </div>
            )}

            {/* Force Deploy Option */}
            {!gateStatus.can_deploy && (
              <div className="space-y-3 pt-2 border-t">
                <div className="flex items-center gap-2">
                  <Warning className="h-4 w-4 text-amber-600" />
                  <Label className="text-sm font-medium text-amber-800">
                    Force Deploy (Not Recommended)
                  </Label>
                </div>
                <p className="text-xs text-muted-foreground">
                  Bypassing gates may result in deploying a lower quality version.
                  This action will be logged for audit purposes.
                </p>
                <Textarea
                  placeholder="Explain why you are bypassing the gates..."
                  value={forceDeployReason}
                  onChange={(e) => setForceDeployReason(e.target.value)}
                  rows={2}
                />
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="confirm-force"
                    checked={confirmForce}
                    onCheckedChange={(checked) =>
                      setConfirmForce(checked as boolean)
                    }
                  />
                  <Label htmlFor="confirm-force" className="text-sm">
                    I understand the risks and want to proceed
                  </Label>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <p>Failed to load gate status</p>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          {gateStatus?.can_deploy ? (
            <Button
              onClick={() => handleDeploy(false)}
              disabled={isDeploying}
              className="gap-2"
            >
              {isDeploying ? (
                <>
                  <CircleNotch className="h-4 w-4 animate-spin" />
                  Deploying...
                </>
              ) : (
                <>
                  <Rocket className="h-4 w-4" />
                  Deploy
                </>
              )}
            </Button>
          ) : (
            <Button
              variant="destructive"
              onClick={() => handleDeploy(true)}
              disabled={isDeploying || !canForceDeploy}
              className="gap-2"
            >
              {isDeploying ? (
                <>
                  <CircleNotch className="h-4 w-4 animate-spin" />
                  Deploying...
                </>
              ) : (
                <>
                  <Warning className="h-4 w-4" />
                  Force Deploy
                </>
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
