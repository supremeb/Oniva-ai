'use client';

import { memo, useState } from 'react';
import { PlugsConnected } from '@phosphor-icons/react';
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useEnabledConnectors } from '@/hooks/useMarketplace';
import { cn } from '@/lib/utils';
import Link from 'next/link';

interface ConnectorPopoverProps {
    sessionId: string;
    activeConnectorIds: string[];
    onToggle: (connectorId: string) => void;
}

export const ConnectorPopover = memo(function ConnectorPopover({
    sessionId,
    activeConnectorIds,
    onToggle,
}: ConnectorPopoverProps) {
    const [open, setOpen] = useState(false);
    const { data, isLoading, error } = useEnabledConnectors();
    const connectors = data?.connectors ?? [];
    const activeCount = activeConnectorIds.length;

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <Tooltip>
                <TooltipTrigger asChild>
                    <PopoverTrigger asChild>
                        <button
                            className={cn(
                                'relative h-8 w-8 flex items-center justify-center rounded-lg',
                                'transition-colors duration-150',
                                activeCount > 0
                                    ? 'text-neutral-900 hover:text-black hover:bg-neutral-100'
                                    : 'text-neutral-400 hover:text-neutral-600 hover:bg-neutral-100'
                            )}
                            aria-label={activeCount > 0 ? `${activeCount} connectors active` : 'Manage connectors'}
                        >
                            <PlugsConnected className="h-[18px] w-[18px]" weight="regular" />
                            {activeCount > 0 && (
                                <span className="absolute -top-1 -right-1 h-4 min-w-[16px] rounded-full bg-neutral-900 text-[10px] text-white flex items-center justify-center font-medium leading-none px-1">
                                    {activeCount}
                                </span>
                            )}
                        </button>
                    </PopoverTrigger>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8}>
                    <p>{activeCount > 0 ? `${activeCount} connectors active` : 'Manage connectors'}</p>
                </TooltipContent>
            </Tooltip>

            <PopoverContent side="top" align="start" className="w-[340px] p-0">
                <div className="px-4 py-3 border-b border-neutral-100">
                    <h3 className="text-sm font-medium text-neutral-900">Connectors</h3>
                    <p className="text-xs text-neutral-500 mt-0.5">Toggle connectors for this session</p>
                </div>

                <div className="max-h-[320px] overflow-y-auto">
                    {isLoading && (
                        <div className="px-4 py-6 text-center">
                            <div className="text-sm text-neutral-400">Loading connectors...</div>
                        </div>
                    )}

                    {error && (
                        <div className="px-4 py-6 text-center">
                            <div className="text-sm text-red-500">Failed to load connectors</div>
                        </div>
                    )}

                    {!isLoading && !error && connectors.length === 0 && (
                        <div className="px-4 py-6 text-center">
                            <div className="text-sm text-neutral-500">No connectors configured</div>
                            <Link
                                href="/settings/integrations/connectors"
                                className="text-xs text-neutral-600 hover:text-neutral-900 mt-1 inline-block"
                            >
                                Set up connectors
                            </Link>
                        </div>
                    )}

                    {!isLoading && !error && connectors.length > 0 && (
                        <div className="py-1">
                            {connectors.map((connector) => {
                                const isActive = activeConnectorIds.includes(connector.id);
                                return (
                                    <div
                                        key={connector.id}
                                        className="flex items-center justify-between px-4 py-2.5 hover:bg-neutral-50 transition-colors"
                                    >
                                        <div className="flex items-center gap-3 min-w-0">
                                            {connector.icon ? (
                                                <span className="text-lg shrink-0">{connector.icon}</span>
                                            ) : (
                                                <PlugsConnected className="h-4 w-4 text-neutral-400 shrink-0" />
                                            )}
                                            <div className="min-w-0">
                                                <div className="text-sm font-medium text-neutral-800 truncate">
                                                    {connector.name}
                                                </div>
                                                <div className="text-xs text-neutral-400 truncate">
                                                    {connector.category}
                                                </div>
                                            </div>
                                        </div>
                                        <Switch
                                            checked={isActive}
                                            onCheckedChange={() => onToggle(connector.id)}
                                            className={isActive ? 'data-[state=checked]:bg-neutral-900' : ''}
                                        />
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>

                <div className="px-4 py-2.5 border-t border-neutral-100">
                    <Link
                        href="/settings/integrations/connectors"
                        className="text-xs text-neutral-600 hover:text-neutral-900 font-medium"
                    >
                        + Add connectors
                    </Link>
                </div>
            </PopoverContent>
        </Popover>
    );
});
