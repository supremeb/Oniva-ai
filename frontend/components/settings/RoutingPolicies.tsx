'use client';

import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Trash2, Plus, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import {
  listRoutingPolicies,
  createRoutingPolicy,
  deleteRoutingPolicy,
  resetRoutingDefaults,
  type RoutingPolicy,
  type RoutingPolicyCreate,
} from '@/lib/api/routing-policies';
import type { LLMProvider } from '@/lib/api/llm-providers';
import { cn } from '@/lib/utils';

const TASK_TYPES = ['research', 'analysis', 'code_gen', 'extraction', 'synthesis', 'browser'] as const;

interface RoutingPoliciesProps {
  providers: LLMProvider[];
  isAdmin: boolean;
}

export function RoutingPolicies({ providers, isAdmin }: RoutingPoliciesProps) {
  const [policies, setPolicies] = useState<RoutingPolicy[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [newPolicy, setNewPolicy] = useState<Partial<RoutingPolicyCreate>>({
    task_type: 'research',
    provider: 'auto',
    model_id: 'auto',
    priority: 0,
  });

  const fetchPolicies = useCallback(async () => {
    try {
      setLoading(true);
      const data = await listRoutingPolicies();
      setPolicies(data);
    } catch {
      toast.error('Failed to load routing policies');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchPolicies(); }, [fetchPolicies]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteRoutingPolicy(id);
      setPolicies((prev) => prev.filter((p) => p.id !== id));
      toast.success('Policy deleted');
    } catch {
      toast.error('Failed to delete policy');
    }
  }, []);

  const handleResetDefaults = useCallback(async () => {
    if (!confirm('This will delete all policies and recreate defaults. Continue?')) return;
    try {
      const defaults = await resetRoutingDefaults();
      setPolicies(defaults);
      toast.success('Policies reset to defaults');
    } catch {
      toast.error('Failed to reset defaults');
    }
  }, []);

  const handleAddPolicy = useCallback(async () => {
    if (!newPolicy.name || !newPolicy.task_type || !newPolicy.model_id || !newPolicy.provider) {
      toast.error('Please fill all required fields');
      return;
    }
    try {
      const created = await createRoutingPolicy(newPolicy as RoutingPolicyCreate);
      setPolicies((prev) => [...prev, created]);
      setShowAdd(false);
      setNewPolicy({ task_type: 'research', provider: 'auto', model_id: 'auto', priority: 0 });
      toast.success('Policy created');
    } catch {
      toast.error('Failed to create policy');
    }
  }, [newPolicy]);

  // Build model options from configured providers
  const modelOptions = providers.flatMap((p) => {
    return (p.models || []).map((m) => ({ provider: p.provider_type, model: m }));
  });

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-sm font-semibold text-neutral-900">Model Routing Policies</h3>
            <p className="text-xs text-neutral-500 mt-0.5">
              Map task types to specific LLM models. &quot;auto&quot; uses the default tier-based resolution.
            </p>
          </div>
          {isAdmin && (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleResetDefaults}
                className="text-xs"
              >
                <RotateCcw className="h-3 w-3 mr-1" />
                Reset
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowAdd(true)}
                className="text-xs"
              >
                <Plus className="h-3 w-3 mr-1" />
                Add Policy
              </Button>
            </div>
          )}
        </div>

        {/* Policies table */}
        {loading ? (
          <div className="py-8 text-center text-sm text-neutral-400">Loading...</div>
        ) : policies.length === 0 ? (
          <div className="py-8 text-center text-sm text-neutral-400">
            No routing policies configured. Using default tier-based resolution.
          </div>
        ) : (
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-neutral-50 border-b">
                  <th className="text-left px-3 py-2 font-medium text-neutral-500">Task Type</th>
                  <th className="text-left px-3 py-2 font-medium text-neutral-500">Output Type</th>
                  <th className="text-left px-3 py-2 font-medium text-neutral-500">Model</th>
                  <th className="text-left px-3 py-2 font-medium text-neutral-500">Provider</th>
                  <th className="text-center px-3 py-2 font-medium text-neutral-500">Active</th>
                  {isAdmin && <th className="w-10" />}
                </tr>
              </thead>
              <tbody>
                {policies.map((policy) => (
                  <tr key={policy.id} className="border-b last:border-0 hover:bg-neutral-50/50">
                    <td className="px-3 py-2">
                      <Badge variant="outline" className="text-[10px]">{policy.task_type}</Badge>
                    </td>
                    <td className="px-3 py-2 text-neutral-500">
                      {policy.output_type || '—'}
                    </td>
                    <td className="px-3 py-2 font-mono text-neutral-700">
                      {policy.model_id}
                    </td>
                    <td className="px-3 py-2 text-neutral-500">{policy.provider}</td>
                    <td className="px-3 py-2 text-center">
                      <span className={cn(
                        'inline-block h-2 w-2 rounded-full',
                        policy.is_active ? 'bg-green-500' : 'bg-neutral-300',
                      )} />
                    </td>
                    {isAdmin && (
                      <td className="px-2 py-2">
                        <button
                          onClick={() => handleDelete(policy.id)}
                          className="p-1 rounded text-neutral-400 hover:text-red-500 transition-colors"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Inline add form */}
        {showAdd && isAdmin && (
          <div className="mt-3 p-3 border rounded-lg bg-neutral-50/50 space-y-2">
            <div className="grid grid-cols-4 gap-2">
              <input
                className="px-2 py-1.5 rounded border text-xs"
                placeholder="Policy name"
                value={newPolicy.name || ''}
                onChange={(e) => setNewPolicy((p) => ({ ...p, name: e.target.value }))}
              />
              <select
                className="px-2 py-1.5 rounded border text-xs bg-white"
                value={newPolicy.task_type}
                onChange={(e) => setNewPolicy((p) => ({ ...p, task_type: e.target.value }))}
              >
                {TASK_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <select
                className="px-2 py-1.5 rounded border text-xs bg-white"
                value={`${newPolicy.provider}/${newPolicy.model_id}`}
                onChange={(e) => {
                  const [provider, ...modelParts] = e.target.value.split('/');
                  setNewPolicy((p) => ({ ...p, provider, model_id: modelParts.join('/') }));
                }}
              >
                <option value="auto/auto">auto (tier-based)</option>
                {modelOptions.map((opt) => (
                  <option key={`${opt.provider}/${opt.model}`} value={`${opt.provider}/${opt.model}`}>
                    {opt.model}
                  </option>
                ))}
              </select>
              <div className="flex gap-1">
                <Button size="sm" className="text-xs flex-1" onClick={handleAddPolicy}>Add</Button>
                <Button size="sm" variant="outline" className="text-xs" onClick={() => setShowAdd(false)}>Cancel</Button>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
