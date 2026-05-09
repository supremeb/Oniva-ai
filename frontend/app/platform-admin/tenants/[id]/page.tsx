'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ArrowLeft, ToggleLeft, ToggleRight, ShieldCheck, Lock } from '@phosphor-icons/react';
import {
  getTenantFeatures,
  toggleTenantFeature,
  type AdminFeatureMap,
} from '@/lib/api/platform-admin';

/**
 * Platform Admin — Tenant Detail page.
 * Shows feature toggles grouped by category with operator-only badges.
 */
export default function TenantDetailPage() {
  const params = useParams();
  const tenantId = params.id as string;

  const [data, setData] = useState<AdminFeatureMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);

  const loadFeatures = useCallback(() => {
    getTenantFeatures(tenantId)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [tenantId]);

  useEffect(() => {
    loadFeatures();
  }, [loadFeatures]);

  const handleToggle = async (flagName: string, currentValue: boolean) => {
    setToggling(flagName);
    setError(null);
    try {
      const updated = await toggleTenantFeature(tenantId, flagName, !currentValue);
      setData(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to toggle feature');
    } finally {
      setToggling(null);
    }
  };

  if (loading) {
    return <div className="text-muted-foreground">Loading tenant features...</div>;
  }

  if (error && !data) {
    return <div className="text-destructive">Error: {error}</div>;
  }

  if (!data) return null;

  // Group flags by category from metadata
  const groups: Record<string, Array<{
    name: string;
    enabled: boolean;
    isOverride: boolean;
    description: string;
    operatorOnly: boolean;
  }>> = {};

  for (const [flagName, meta] of Object.entries(data.metadata)) {
    const group = meta.group;
    if (!groups[group]) groups[group] = [];
    groups[group].push({
      name: flagName,
      enabled: data.features[flagName] ?? false,
      isOverride: flagName in data.overrides,
      description: meta.description,
      operatorOnly: meta.operator_only,
    });
  }

  // Also add flags that are in features but not in metadata (global flags)
  const metaFlags = new Set(Object.keys(data.metadata));
  const ungrouped = Object.entries(data.features)
    .filter(([name]) => !metaFlags.has(name))
    .map(([name, enabled]) => ({
      name,
      enabled,
      isOverride: name in data.overrides,
      description: '',
      operatorOnly: false,
    }));
  if (ungrouped.length > 0) {
    groups['Other'] = ungrouped;
  }

  const groupOrder = ['Core', 'Finance', 'Infrastructure', 'Other'];
  const sortedGroups = Object.entries(groups).sort(
    ([a], [b]) => (groupOrder.indexOf(a) === -1 ? 99 : groupOrder.indexOf(a)) -
                  (groupOrder.indexOf(b) === -1 ? 99 : groupOrder.indexOf(b))
  );

  return (
    <div className="max-w-3xl">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Link href="/platform-admin/tenants" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h2 className="text-xl font-semibold">Tenant Features</h2>
          <p className="text-sm text-muted-foreground font-mono">{tenantId}</p>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 text-sm text-destructive bg-destructive/10 rounded-lg">
          {error}
        </div>
      )}

      {/* Feature Groups */}
      <div className="space-y-6">
        {sortedGroups.map(([group, flags]) => (
          <div key={group} className="border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 bg-muted/50 border-b border-border">
              <h3 className="text-sm font-semibold flex items-center gap-2">
                {group === 'Infrastructure' && <Lock className="h-4 w-4" />}
                {group === 'Finance' && <ShieldCheck className="h-4 w-4" />}
                {group}
              </h3>
            </div>
            <div className="divide-y divide-border">
              {flags.map((flag) => (
                <div
                  key={flag.name}
                  className="flex items-center justify-between px-4 py-3 hover:bg-muted/20 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-mono text-foreground">{flag.name}</span>
                      {flag.operatorOnly && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 font-medium">
                          operator-only
                        </span>
                      )}
                      {flag.isOverride && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">
                          override
                        </span>
                      )}
                    </div>
                    {flag.description && (
                      <p className="text-xs text-muted-foreground mt-0.5">{flag.description}</p>
                    )}
                  </div>

                  <button
                    onClick={() => handleToggle(flag.name, flag.enabled)}
                    disabled={toggling === flag.name}
                    className="ml-4 shrink-0 transition-colors disabled:opacity-50"
                    aria-label={`Toggle ${flag.name}`}
                  >
                    {flag.enabled ? (
                      <ToggleRight className="h-7 w-7 text-primary" weight="fill" />
                    ) : (
                      <ToggleLeft className="h-7 w-7 text-muted-foreground" weight="regular" />
                    )}
                  </button>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
