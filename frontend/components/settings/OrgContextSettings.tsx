'use client';

import { useState } from 'react';
import { Buildings, FloppyDisk, Plus, X, Brain, Info } from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import { useOrgProfile, useUpdateOrgProfile } from '@/hooks/useMemory';

export function OrgContextSettings() {
    const { data: profile, isLoading, error } = useOrgProfile();
    const updateProfile = useUpdateOrgProfile();

    const [industry, setIndustry] = useState('');
    const [description, setDescription] = useState('');
    const [defaultContext, setDefaultContext] = useState('');
    const [frameworks, setFrameworks] = useState<string[]>([]);
    const [newFramework, setNewFramework] = useState('');
    const [initialized, setInitialized] = useState(false);

    // Initialize form from profile data
    if (profile && !initialized) {
        setIndustry(profile.industry || '');
        setDescription(profile.description || '');
        setDefaultContext(profile.default_context || '');
        setFrameworks(profile.compliance_frameworks || []);
        setInitialized(true);
    }

    const handleSave = async () => {
        try {
            await updateProfile.mutateAsync({
                industry: industry || undefined,
                description: description || undefined,
                compliance_frameworks: frameworks,
                default_context: defaultContext || undefined,
            });
            toast('Organization profile updated', { description: 'Changes will be reflected in new agent conversations.' });
        } catch (err) {
            toast.error('Error', { description: err instanceof Error ? err.message : 'Failed to update org profile.' });
        }
    };

    const addFramework = () => {
        const trimmed = newFramework.trim();
        if (trimmed && !frameworks.includes(trimmed)) {
            setFrameworks([...frameworks, trimmed]);
            setNewFramework('');
        }
    };

    const removeFramework = (fw: string) => {
        setFrameworks(frameworks.filter((f) => f !== fw));
    };

    if (error) {
        return (
            <div className="rounded-xl border border-border p-6 text-center">
                <Buildings weight="duotone" className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
                <p className="text-sm text-muted-foreground mb-1">
                    Organization Context is not enabled
                </p>
                <p className="text-xs text-muted-foreground">
                    Set <code className="px-1 py-0.5 bg-muted rounded text-xs">USE_ORG_CONTEXT=true</code> in your backend configuration to enable this feature.
                </p>
            </div>
        );
    }

    if (isLoading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-10 bg-muted rounded-lg" />
                <div className="h-24 bg-muted rounded-lg" />
                <div className="h-32 bg-muted rounded-lg" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Info banner */}
            <div className="flex items-start gap-3 p-4 bg-muted/50 rounded-lg border border-border/60">
                <Info weight="duotone" className="h-5 w-5 text-primary mt-0.5 shrink-0" />
                <div className="text-sm text-muted-foreground">
                    <p>
                        The organization profile and shared memories are injected into every agent prompt for your tenant.
                        This helps agents understand your organization&apos;s context without repeating information.
                    </p>
                    {profile && (
                        <p className="mt-2 text-xs">
                            <Brain weight="duotone" className="inline h-3.5 w-3.5 mr-1" />
                            <strong>{profile.shared_memory_count}</strong> shared memories in your organization
                        </p>
                    )}
                </div>
            </div>

            {/* Industry */}
            <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Industry</label>
                <Input
                    value={industry}
                    onChange={(e) => setIndustry(e.target.value)}
                    placeholder="e.g. Financial Services, Healthcare, Legal"
                    maxLength={100}
                />
                <p className="text-xs text-muted-foreground">
                    Helps agents tailor analysis and language to your industry
                </p>
            </div>

            {/* Description */}
            <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Organization Description</label>
                <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Brief description of your organization..."
                    maxLength={2000}
                    rows={3}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
            </div>

            {/* Compliance Frameworks */}
            <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Compliance Frameworks</label>
                <div className="flex flex-wrap gap-2 mb-2">
                    {frameworks.map((fw) => (
                        <span
                            key={fw}
                            className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-primary/10 text-primary rounded-md"
                        >
                            {fw}
                            <button
                                onClick={() => removeFramework(fw)}
                                className="hover:text-destructive transition-colors"
                            >
                                <X className="h-3 w-3" />
                            </button>
                        </span>
                    ))}
                </div>
                <div className="flex gap-2">
                    <Input
                        value={newFramework}
                        onChange={(e) => setNewFramework(e.target.value)}
                        placeholder="e.g. SOX, GDPR, HIPAA"
                        onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addFramework())}
                    />
                    <Button variant="outline" size="sm" onClick={addFramework} disabled={!newFramework.trim()}>
                        <Plus className="h-4 w-4" />
                    </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                    Frameworks are passed to compliance agents automatically
                </p>
            </div>

            {/* Default Context */}
            <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">Default Agent Context</label>
                <textarea
                    value={defaultContext}
                    onChange={(e) => setDefaultContext(e.target.value)}
                    placeholder="Context injected into every agent prompt, e.g.:\n\nWe are a mid-cap asset manager focused on ESG-compliant investments. Our primary market is North American equities..."
                    maxLength={5000}
                    rows={6}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring font-mono"
                />
                <p className="text-xs text-muted-foreground">
                    This text is prepended to every agent interaction. Keep it concise (&lt;500 tokens recommended).
                </p>
            </div>

            {/* Save button */}
            <div className="flex justify-end pt-4 border-t border-border/60">
                <Button onClick={handleSave} disabled={updateProfile.isPending}>
                    <FloppyDisk className="h-4 w-4 mr-2" />
                    {updateProfile.isPending ? 'Saving...' : 'Save Changes'}
                </Button>
            </div>
        </div>
    );
}
