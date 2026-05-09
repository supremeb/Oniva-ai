'use client';

import { useState } from 'react';
import { Lightning, Clock, Globe, Link } from '@phosphor-icons/react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { SimpleCollapsibleSection } from './CollapsibleSection';
import { FeatureGate } from '@/components/ui/FeatureGate';
import { cn } from '@/lib/utils';

interface BackgroundConfig {
  enabled?: boolean;
  cron_expression?: string;
  timezone?: string;
  webhook_url?: string;
  webhook_headers?: Record<string, string>;
}

interface ResourceConstraints {
  max_concurrent?: number;
  token_budget?: number;
  timeout_seconds?: number;
}

interface BackgroundTabProps {
  backgroundConfig: BackgroundConfig | null;
  resourceConstraints: ResourceConstraints;
  cronExpression: string;
  timezone: string;
  onSave: (config: {
    background_config: BackgroundConfig;
    resource_constraints: ResourceConstraints;
    cron_expression: string;
    timezone: string;
  }) => void;
}

const TIMEZONES = [
  'UTC', 'America/New_York', 'America/Chicago', 'America/Denver',
  'America/Los_Angeles', 'Europe/London', 'Europe/Paris', 'Europe/Berlin',
  'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Australia/Sydney',
];

/**
 * Background tab for Agent Studio — configure autonomous execution.
 * Minimal: enable toggle, cron expression, timezone, webhook URL, limits.
 */
export function BackgroundTab({
  backgroundConfig,
  resourceConstraints,
  cronExpression: initialCron,
  timezone: initialTz,
  onSave,
}: BackgroundTabProps) {
  const [enabled, setEnabled] = useState(backgroundConfig?.enabled ?? false);
  const [cron, setCron] = useState(initialCron || '');
  const [tz, setTz] = useState(initialTz || 'UTC');
  const [webhookUrl, setWebhookUrl] = useState(backgroundConfig?.webhook_url ?? '');
  const [maxConcurrent, setMaxConcurrent] = useState(resourceConstraints.max_concurrent ?? 1);
  const [tokenBudget, setTokenBudget] = useState(resourceConstraints.token_budget ?? 50000);
  const [timeoutSeconds, setTimeoutSeconds] = useState(resourceConstraints.timeout_seconds ?? 1800);
  const [dirty, setDirty] = useState(false);

  const handleSave = () => {
    onSave({
      background_config: {
        enabled,
        webhook_url: webhookUrl,
        webhook_headers: backgroundConfig?.webhook_headers,
      },
      resource_constraints: {
        max_concurrent: maxConcurrent,
        token_budget: tokenBudget,
        timeout_seconds: timeoutSeconds,
      },
      cron_expression: cron,
      timezone: tz,
    });
    setDirty(false);
  };

  const markDirty = () => setDirty(true);

  return (
    <FeatureGate flag="ENABLE_BACKGROUND_AGENTS" fallback={
      <div className="p-4 text-[13px] leading-5 text-muted-foreground">
        Background agents are not enabled for this tenant. Contact your platform operator.
      </div>
    }>
      <ScrollArea className="h-full">
        <div className="p-3 space-y-3">
          {/* Enable Toggle */}
          <SimpleCollapsibleSection
            title="Background Mode"
            icon={<Lightning className="h-4 w-4" weight="duotone" />}
            defaultOpen={true}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[11px] leading-4 text-muted-foreground">
                  Run this agent autonomously on a schedule or via webhook
                </p>
              </div>
              <button
                onClick={() => { setEnabled(!enabled); markDirty(); }}
                className={cn(
                  'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors',
                  enabled ? 'bg-primary' : 'bg-muted'
                )}
              >
                <span className={cn(
                  'pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform',
                  enabled ? 'translate-x-4' : 'translate-x-0'
                )} />
              </button>
            </div>
          </SimpleCollapsibleSection>

          {enabled && (
            <>
              {/* Schedule */}
              <SimpleCollapsibleSection
                title="Schedule"
                icon={<Clock className="h-4 w-4" weight="duotone" />}
                defaultOpen={true}
              >
                <div className="space-y-2.5">
                  <div className="space-y-1.5">
                    <Label className="text-xs font-medium leading-none">Cron Expression</Label>
                    <Input
                      value={cron}
                      onChange={(e) => { setCron(e.target.value); markDirty(); }}
                      placeholder="0 9 * * 1 (Mondays at 9am)"
                      className="h-8 font-mono text-xs"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <Globe className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    <Select value={tz} onValueChange={(v) => { setTz(v); markDirty(); }}>
                      <SelectTrigger className="h-7 text-xs w-auto min-w-[160px]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {TIMEZONES.map((t) => (
                          <SelectItem key={t} value={t}>{t}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <a
                      href="https://crontab.guru/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[11px] text-primary hover:underline ml-auto"
                    >
                      crontab.guru
                    </a>
                  </div>
                </div>
              </SimpleCollapsibleSection>

              {/* Webhook URL */}
              <SimpleCollapsibleSection
                title="Webhook"
                icon={<Link className="h-4 w-4" weight="duotone" />}
                defaultOpen={false}
              >
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium leading-none">
                    Webhook URL <span className="text-muted-foreground font-normal">(optional)</span>
                  </Label>
                  <Input
                    type="url"
                    value={webhookUrl}
                    onChange={(e) => { setWebhookUrl(e.target.value); markDirty(); }}
                    placeholder="https://hooks.slack.com/services/..."
                    className="h-8 text-xs"
                  />
                  <p className="text-[11px] leading-4 text-muted-foreground">
                    Agent results will be POSTed as JSON to this URL when the run completes.
                  </p>
                </div>
              </SimpleCollapsibleSection>

              {/* Limits */}
              <SimpleCollapsibleSection
                title="Limits"
                icon={<Lightning className="h-4 w-4" weight="duotone" />}
                defaultOpen={false}
              >
                <div className="grid grid-cols-3 gap-2.5">
                  <div className="space-y-1">
                    <label className="text-[10px] leading-none text-muted-foreground uppercase tracking-wider">Max concurrent</label>
                    <Input
                      type="number"
                      value={maxConcurrent}
                      onChange={(e) => { setMaxConcurrent(parseInt(e.target.value) || 1); markDirty(); }}
                      min={1}
                      max={10}
                      className="h-8 text-xs"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-[10px] leading-none text-muted-foreground uppercase tracking-wider">Token budget</label>
                    <Input
                      type="number"
                      value={tokenBudget}
                      onChange={(e) => { setTokenBudget(parseInt(e.target.value) || 50000); markDirty(); }}
                      min={1000}
                      step={5000}
                      className="h-8 text-xs"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-[10px] leading-none text-muted-foreground uppercase tracking-wider">Timeout (sec)</label>
                    <Input
                      type="number"
                      value={timeoutSeconds}
                      onChange={(e) => { setTimeoutSeconds(parseInt(e.target.value) || 1800); markDirty(); }}
                      min={60}
                      step={300}
                      className="h-8 text-xs"
                    />
                  </div>
                </div>
              </SimpleCollapsibleSection>

              {/* Save */}
              {dirty && (
                <Button
                  onClick={handleSave}
                  size="sm"
                  className="w-full h-8 text-xs"
                >
                  Save Background Config
                </Button>
              )}
            </>
          )}
        </div>
      </ScrollArea>
    </FeatureGate>
  );
}
