'use client';

import { useState } from 'react';
import { ArrowsClockwise, ShieldWarning, Timer, Database, Plus, Trash, CaretDown, CaretUp } from '@phosphor-icons/react';

// Adornment type definitions matching backend
export interface RetryAdornment {
    type: 'retry';
    maxRetries: number;
    backoffSeconds: number;
    backoffMultiplier: number;
    retryOnErrors?: string[];
    retryOnStatuses?: string[];
}

export interface TryAdornment {
    type: 'try';
    fallbackValue: any;
    catchAll: boolean;
    catchErrors?: string[];
    outputOnError?: Record<string, any>;
}

export interface TimeoutAdornment {
    type: 'timeout';
    timeoutSeconds: number;
    fallbackOnTimeout: boolean;
    fallbackValue?: any;
}

export interface CacheAdornment {
    type: 'cache';
    ttlSeconds: number;
    cacheKey?: string;
    cacheInputs: boolean;
    cacheVariables?: string[];
}

export type Adornment = RetryAdornment | TryAdornment | TimeoutAdornment | CacheAdornment;

interface AdornmentsPanelProps {
    adornments: Adornment[];
    onChange: (adornments: Adornment[]) => void;
}

const ADORNMENT_TYPES = [
    { type: 'retry', label: 'Retry', icon: ArrowsClockwise, color: 'text-blue-600', bg: 'bg-blue-50' },
    { type: 'try', label: 'Try/Catch', icon: ShieldWarning, color: 'text-amber-600', bg: 'bg-amber-50' },
    { type: 'timeout', label: 'Timeout', icon: Timer, color: 'text-purple-600', bg: 'bg-purple-50' },
    { type: 'cache', label: 'Cache', icon: Database, color: 'text-green-600', bg: 'bg-green-50' },
] as const;

const DEFAULT_ADORNMENTS: Record<string, Adornment> = {
    retry: {
        type: 'retry',
        maxRetries: 3,
        backoffSeconds: 1,
        backoffMultiplier: 2,
        retryOnStatuses: ['failed'],
    },
    try: {
        type: 'try',
        fallbackValue: null,
        catchAll: true,
    },
    timeout: {
        type: 'timeout',
        timeoutSeconds: 60,
        fallbackOnTimeout: false,
    },
    cache: {
        type: 'cache',
        ttlSeconds: 3600,
        cacheInputs: true,
    },
};

export function AdornmentsPanel({ adornments, onChange }: AdornmentsPanelProps) {
    const [expandedIndex, setExpandedIndex] = useState<number | null>(null);
    const [showAddMenu, setShowAddMenu] = useState(false);

    const addAdornment = (type: string) => {
        const newAdornment = { ...DEFAULT_ADORNMENTS[type] };
        onChange([...adornments, newAdornment]);
        setExpandedIndex(adornments.length);
        setShowAddMenu(false);
    };

    const removeAdornment = (index: number) => {
        const updated = adornments.filter((_, i) => i !== index);
        onChange(updated);
        if (expandedIndex === index) setExpandedIndex(null);
    };

    const updateAdornment = (index: number, updates: Partial<Adornment>) => {
        const updated = adornments.map((a, i) =>
            i === index ? { ...a, ...updates } : a
        );
        onChange(updated as Adornment[]);
    };

    const moveAdornment = (index: number, direction: 'up' | 'down') => {
        const newIndex = direction === 'up' ? index - 1 : index + 1;
        if (newIndex < 0 || newIndex >= adornments.length) return;

        const updated = [...adornments];
        [updated[index], updated[newIndex]] = [updated[newIndex], updated[index]];
        onChange(updated);
        setExpandedIndex(newIndex);
    };

    const getAdornmentMeta = (type: string) =>
        ADORNMENT_TYPES.find((t) => t.type === type) || ADORNMENT_TYPES[0];

    const existingTypes = adornments.map((a) => a.type);
    const availableTypes = ADORNMENT_TYPES.filter((t) => !existingTypes.includes(t.type));

    return (
        <div className="space-y-3">
            <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-muted-foreground uppercase">
                    Node Adornments
                </label>
                <div className="relative">
                    <button
                        onClick={() => setShowAddMenu(!showAddMenu)}
                        disabled={availableTypes.length === 0}
                        className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-primary hover:bg-primary/10 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        <Plus className="w-3 h-3" />
                        Add
                    </button>
                    {showAddMenu && availableTypes.length > 0 && (
                        <div className="absolute right-0 top-full mt-1 z-50 bg-background border rounded-lg shadow-lg p-1 min-w-[140px]">
                            {availableTypes.map(({ type, label, icon: Icon, color, bg }) => (
                                <button
                                    key={type}
                                    onClick={() => addAdornment(type)}
                                    className={`flex items-center gap-2 w-full px-3 py-2 text-xs hover:bg-muted rounded ${bg}`}
                                >
                                    <Icon className={`w-4 h-4 ${color}`} weight="duotone" />
                                    <span className="font-medium">{label}</span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {adornments.length === 0 ? (
                <p className="text-xs text-muted-foreground italic py-2">
                    No adornments configured. Add retry, try/catch, timeout, or caching behavior.
                </p>
            ) : (
                <div className="space-y-2">
                    {adornments.map((adornment, index) => {
                        const meta = getAdornmentMeta(adornment.type);
                        const Icon = meta.icon;
                        const isExpanded = expandedIndex === index;

                        return (
                            <div
                                key={`${adornment.type}-${index}`}
                                className={`border rounded-lg overflow-hidden ${meta.bg}`}
                            >
                                {/* Header */}
                                <div
                                    className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-black/5"
                                    onClick={() => setExpandedIndex(isExpanded ? null : index)}
                                >
                                    <div className="flex items-center gap-2">
                                        <Icon className={`w-4 h-4 ${meta.color}`} weight="duotone" />
                                        <span className="text-xs font-medium">{meta.label}</span>
                                        <span className="text-[10px] text-muted-foreground">
                                            #{index + 1}
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-1">
                                        <button
                                            onClick={(e) => { e.stopPropagation(); moveAdornment(index, 'up'); }}
                                            disabled={index === 0}
                                            className="p-1 hover:bg-black/10 rounded disabled:opacity-30"
                                        >
                                            <CaretUp className="w-3 h-3" />
                                        </button>
                                        <button
                                            onClick={(e) => { e.stopPropagation(); moveAdornment(index, 'down'); }}
                                            disabled={index === adornments.length - 1}
                                            className="p-1 hover:bg-black/10 rounded disabled:opacity-30"
                                        >
                                            <CaretDown className="w-3 h-3" />
                                        </button>
                                        <button
                                            onClick={(e) => { e.stopPropagation(); removeAdornment(index); }}
                                            className="p-1 hover:bg-red-100 text-red-500 rounded"
                                        >
                                            <Trash className="w-3 h-3" />
                                        </button>
                                    </div>
                                </div>

                                {/* Expanded Config */}
                                {isExpanded && (
                                    <div className="px-3 pb-3 border-t border-black/10 pt-3 space-y-3">
                                        {adornment.type === 'retry' && (
                                            <RetryConfig
                                                adornment={adornment}
                                                onChange={(updates) => updateAdornment(index, updates)}
                                            />
                                        )}
                                        {adornment.type === 'try' && (
                                            <TryConfig
                                                adornment={adornment}
                                                onChange={(updates) => updateAdornment(index, updates)}
                                            />
                                        )}
                                        {adornment.type === 'timeout' && (
                                            <TimeoutConfig
                                                adornment={adornment}
                                                onChange={(updates) => updateAdornment(index, updates)}
                                            />
                                        )}
                                        {adornment.type === 'cache' && (
                                            <CacheConfig
                                                adornment={adornment}
                                                onChange={(updates) => updateAdornment(index, updates)}
                                            />
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {adornments.length > 1 && (
                <p className="text-[10px] text-muted-foreground">
                    Adornments execute in order. First adornment is the outermost wrapper.
                </p>
            )}
        </div>
    );
}

// Retry config sub-component
function RetryConfig({
    adornment,
    onChange,
}: {
    adornment: RetryAdornment;
    onChange: (updates: Partial<RetryAdornment>) => void;
}) {
    return (
        <div className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
                <div>
                    <label className="text-[10px] text-muted-foreground">Max Retries</label>
                    <input
                        type="number"
                        min={1}
                        max={10}
                        value={adornment.maxRetries}
                        onChange={(e) => onChange({ maxRetries: parseInt(e.target.value) || 3 })}
                        className="w-full px-2 py-1 text-xs border rounded"
                    />
                </div>
                <div>
                    <label className="text-[10px] text-muted-foreground">Initial Backoff (s)</label>
                    <input
                        type="number"
                        min={0.1}
                        step={0.5}
                        value={adornment.backoffSeconds}
                        onChange={(e) => onChange({ backoffSeconds: parseFloat(e.target.value) || 1 })}
                        className="w-full px-2 py-1 text-xs border rounded"
                    />
                </div>
            </div>
            <div>
                <label className="text-[10px] text-muted-foreground">Backoff Multiplier</label>
                <input
                    type="number"
                    min={1}
                    max={5}
                    step={0.5}
                    value={adornment.backoffMultiplier}
                    onChange={(e) => onChange({ backoffMultiplier: parseFloat(e.target.value) || 2 })}
                    className="w-full px-2 py-1 text-xs border rounded"
                />
                <p className="text-[10px] text-muted-foreground mt-1">
                    Delays: {adornment.backoffSeconds}s, {(adornment.backoffSeconds * adornment.backoffMultiplier).toFixed(1)}s, {(adornment.backoffSeconds * adornment.backoffMultiplier * adornment.backoffMultiplier).toFixed(1)}s...
                </p>
            </div>
        </div>
    );
}

// Try/Catch config sub-component
function TryConfig({
    adornment,
    onChange,
}: {
    adornment: TryAdornment;
    onChange: (updates: Partial<TryAdornment>) => void;
}) {
    return (
        <div className="space-y-2">
            <div className="flex items-center gap-2">
                <input
                    type="checkbox"
                    id="catchAll"
                    checked={adornment.catchAll}
                    onChange={(e) => onChange({ catchAll: e.target.checked })}
                    className="rounded"
                />
                <label htmlFor="catchAll" className="text-xs">
                    Catch all errors
                </label>
            </div>
            <div>
                <label className="text-[10px] text-muted-foreground">Fallback Value (JSON)</label>
                <input
                    type="text"
                    value={adornment.fallbackValue === null ? 'null' : JSON.stringify(adornment.fallbackValue)}
                    onChange={(e) => {
                        try {
                            onChange({ fallbackValue: JSON.parse(e.target.value) });
                        } catch {
                            // Keep as string if invalid JSON
                            onChange({ fallbackValue: e.target.value });
                        }
                    }}
                    className="w-full px-2 py-1 text-xs border rounded font-mono"
                    placeholder="null"
                />
            </div>
            {!adornment.catchAll && (
                <div>
                    <label className="text-[10px] text-muted-foreground">Error Types to Catch (comma-separated)</label>
                    <input
                        type="text"
                        value={(adornment.catchErrors || []).join(', ')}
                        onChange={(e) => onChange({
                            catchErrors: e.target.value.split(',').map((s) => s.trim()).filter(Boolean)
                        })}
                        className="w-full px-2 py-1 text-xs border rounded"
                        placeholder="timeout, connection"
                    />
                </div>
            )}
        </div>
    );
}

// Timeout config sub-component
function TimeoutConfig({
    adornment,
    onChange,
}: {
    adornment: TimeoutAdornment;
    onChange: (updates: Partial<TimeoutAdornment>) => void;
}) {
    return (
        <div className="space-y-2">
            <div>
                <label className="text-[10px] text-muted-foreground">Timeout (seconds)</label>
                <input
                    type="number"
                    min={1}
                    max={600}
                    value={adornment.timeoutSeconds}
                    onChange={(e) => onChange({ timeoutSeconds: parseInt(e.target.value) || 60 })}
                    className="w-full px-2 py-1 text-xs border rounded"
                />
            </div>
            <div className="flex items-center gap-2">
                <input
                    type="checkbox"
                    id="fallbackOnTimeout"
                    checked={adornment.fallbackOnTimeout}
                    onChange={(e) => onChange({ fallbackOnTimeout: e.target.checked })}
                    className="rounded"
                />
                <label htmlFor="fallbackOnTimeout" className="text-xs">
                    Return fallback value on timeout (instead of failing)
                </label>
            </div>
            {adornment.fallbackOnTimeout && (
                <div>
                    <label className="text-[10px] text-muted-foreground">Fallback Value (JSON)</label>
                    <input
                        type="text"
                        value={adornment.fallbackValue === undefined ? '' : JSON.stringify(adornment.fallbackValue)}
                        onChange={(e) => {
                            try {
                                onChange({ fallbackValue: JSON.parse(e.target.value) });
                            } catch {
                                onChange({ fallbackValue: e.target.value });
                            }
                        }}
                        className="w-full px-2 py-1 text-xs border rounded font-mono"
                        placeholder="null"
                    />
                </div>
            )}
        </div>
    );
}

// Cache config sub-component
function CacheConfig({
    adornment,
    onChange,
}: {
    adornment: CacheAdornment;
    onChange: (updates: Partial<CacheAdornment>) => void;
}) {
    return (
        <div className="space-y-2">
            <div>
                <label className="text-[10px] text-muted-foreground">TTL (seconds)</label>
                <input
                    type="number"
                    min={60}
                    max={86400}
                    value={adornment.ttlSeconds}
                    onChange={(e) => onChange({ ttlSeconds: parseInt(e.target.value) || 3600 })}
                    className="w-full px-2 py-1 text-xs border rounded"
                />
                <p className="text-[10px] text-muted-foreground mt-1">
                    {adornment.ttlSeconds >= 3600
                        ? `${(adornment.ttlSeconds / 3600).toFixed(1)} hours`
                        : `${(adornment.ttlSeconds / 60).toFixed(0)} minutes`}
                </p>
            </div>
            <div className="flex items-center gap-2">
                <input
                    type="checkbox"
                    id="cacheInputs"
                    checked={adornment.cacheInputs}
                    onChange={(e) => onChange({ cacheInputs: e.target.checked })}
                    className="rounded"
                />
                <label htmlFor="cacheInputs" className="text-xs">
                    Include inputs in cache key
                </label>
            </div>
            <div>
                <label className="text-[10px] text-muted-foreground">Custom Cache Key Prefix (optional)</label>
                <input
                    type="text"
                    value={adornment.cacheKey || ''}
                    onChange={(e) => onChange({ cacheKey: e.target.value || undefined })}
                    className="w-full px-2 py-1 text-xs border rounded"
                    placeholder="Uses node ID by default"
                />
            </div>
        </div>
    );
}
