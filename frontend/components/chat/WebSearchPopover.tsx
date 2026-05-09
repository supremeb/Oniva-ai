'use client';

import { memo, useState, useCallback, useRef } from 'react';
import { GlobeSimple, X, Plus } from '@phosphor-icons/react';
import {
    Popover,
    PopoverContent,
    PopoverAnchor,
} from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';

interface WebSearchPopoverProps {
    webSearchEnabled: boolean;
    specificSites: string[];
    onToggleWebSearch: () => void;
    onAddSite: (url: string) => void;
    onRemoveSite: (url: string) => void;
}

export const WebSearchPopover = memo(function WebSearchPopover({
    webSearchEnabled,
    specificSites,
    onToggleWebSearch,
    onAddSite,
    onRemoveSite,
}: WebSearchPopoverProps) {
    const [open, setOpen] = useState(false);
    const [siteInput, setSiteInput] = useState('');
    const [inputError, setInputError] = useState('');
    const siteCount = specificSites.length;
    const buttonRef = useRef<HTMLButtonElement>(null);

    const handleAddSite = useCallback(() => {
        const trimmed = siteInput.trim();
        if (!trimmed) return;

        let urlStr = trimmed;
        if (!/^https?:\/\//i.test(urlStr)) {
            urlStr = `https://${urlStr}`;
        }

        try {
            const parsed = new URL(urlStr);
            const domain = parsed.hostname;
            if (specificSites.includes(domain)) {
                setInputError('Site already added');
                return;
            }
            if (specificSites.length >= 10) {
                setInputError('Maximum 10 sites');
                return;
            }
            onAddSite(domain);
            setSiteInput('');
            setInputError('');
        } catch {
            setInputError('Invalid URL');
        }
    }, [siteInput, specificSites, onAddSite]);

    // Click handler: auto-enable web search + open popover
    const handleClick = useCallback(() => {
        if (!webSearchEnabled) {
            onToggleWebSearch();
        }
        setOpen((prev) => !prev);
    }, [webSearchEnabled, onToggleWebSearch]);

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverAnchor asChild>
                <button
                    ref={buttonRef}
                    type="button"
                    onClick={handleClick}
                    className={cn(
                        'relative h-8 w-8 flex items-center justify-center rounded-lg',
                        'transition-colors duration-150',
                        webSearchEnabled
                            ? 'text-neutral-900 hover:text-black hover:bg-neutral-100'
                            : 'text-neutral-400 hover:text-neutral-600 hover:bg-neutral-100'
                    )}
                    aria-label={webSearchEnabled ? 'Web search enabled' : 'Enable web search'}
                >
                    <GlobeSimple className="h-[18px] w-[18px]" weight={webSearchEnabled ? 'fill' : 'regular'} />
                    {siteCount > 0 && (
                        <span className="absolute -top-1 -right-1 h-4 min-w-[16px] rounded-full bg-neutral-900 text-[10px] text-white flex items-center justify-center font-medium leading-none px-1">
                            {siteCount}
                        </span>
                    )}
                </button>
            </PopoverAnchor>

            <PopoverContent side="top" align="start" className="w-[340px] p-0">
                {/* Header with toggle */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-100">
                    <div>
                        <h3 className="text-sm font-medium text-neutral-900">Web Search</h3>
                        <p className="text-xs text-neutral-500 mt-0.5">Search the web for answers</p>
                    </div>
                    <Switch
                        checked={webSearchEnabled}
                        onCheckedChange={onToggleWebSearch}
                        className={webSearchEnabled ? 'data-[state=checked]:bg-neutral-900' : ''}
                    />
                </div>

                {/* Specific sites section */}
                {webSearchEnabled && (
                    <div className="px-4 py-3">
                        <div className="text-xs font-medium text-neutral-500 mb-2">
                            Restrict to specific sites {siteCount > 0 && `(${siteCount}/10)`}
                        </div>

                        {/* Site list */}
                        {specificSites.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 mb-2.5">
                                {specificSites.map((site) => (
                                    <span
                                        key={site}
                                        className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-md bg-neutral-100 text-neutral-800 border border-neutral-200 text-xs font-medium"
                                    >
                                        {site}
                                        <button
                                            onClick={() => onRemoveSite(site)}
                                            className="hover:text-neutral-900 p-0.5"
                                            aria-label={`Remove ${site}`}
                                        >
                                            <X size={12} />
                                        </button>
                                    </span>
                                ))}
                            </div>
                        )}

                        {/* Add site input */}
                        {siteCount < 10 && (
                            <div className="flex gap-1.5">
                                <input
                                    type="text"
                                    value={siteInput}
                                    onChange={(e) => {
                                        setSiteInput(e.target.value);
                                        setInputError('');
                                    }}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter') {
                                            e.preventDefault();
                                            handleAddSite();
                                        }
                                    }}
                                    placeholder="example.com"
                                    className={cn(
                                        'flex-1 px-2.5 py-1.5 text-xs rounded-md border bg-white',
                                        'placeholder:text-neutral-400 text-neutral-800',
                                        'focus:outline-none focus:ring-1 focus:ring-blue-300 focus:border-blue-300',
                                        inputError ? 'border-red-300' : 'border-neutral-200'
                                    )}
                                />
                                <button
                                    onClick={handleAddSite}
                                    className="h-7 w-7 flex items-center justify-center rounded-md bg-neutral-100 text-neutral-700 hover:bg-neutral-200 transition-colors"
                                    aria-label="Add site"
                                >
                                    <Plus size={14} weight="bold" />
                                </button>
                            </div>
                        )}
                        {inputError && (
                            <p className="text-[11px] text-red-500 mt-1">{inputError}</p>
                        )}
                    </div>
                )}
            </PopoverContent>
        </Popover>
    );
});
