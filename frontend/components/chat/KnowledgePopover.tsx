'use client';

import { memo } from 'react';
import { Books } from '@phosphor-icons/react';
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases';
import { cn } from '@/lib/utils';
import Link from 'next/link';

interface KnowledgePopoverProps {
    activeKnowledgeBaseIds: string[];
    onToggleKnowledgeBase: (kbId: string) => void;
}

export const KnowledgePopover = memo(function KnowledgePopover({
    activeKnowledgeBaseIds,
    onToggleKnowledgeBase,
}: KnowledgePopoverProps) {
    const { data: knowledgeBases = [], isLoading, error } = useKnowledgeBases();
    const activeCount = activeKnowledgeBaseIds.length;

    return (
        <Popover>
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
                            aria-label={activeCount > 0 ? `${activeCount} knowledge bases active` : 'Knowledge bases'}
                        >
                            <Books className="h-[18px] w-[18px]" weight="regular" />
                            {activeCount > 0 && (
                                <span className="absolute -top-1 -right-1 h-4 min-w-[16px] rounded-full bg-neutral-900 text-[10px] text-white flex items-center justify-center font-medium leading-none px-1">
                                    {activeCount}
                                </span>
                            )}
                        </button>
                    </PopoverTrigger>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8}>
                    <p>{activeCount > 0 ? `${activeCount} knowledge bases active` : 'Knowledge bases'}</p>
                </TooltipContent>
            </Tooltip>

            <PopoverContent side="top" align="start" className="w-[340px] p-0">
                <div className="px-4 py-3 border-b border-neutral-100">
                    <h3 className="text-sm font-medium text-neutral-900">Knowledge Bases</h3>
                    <p className="text-xs text-neutral-500 mt-0.5">Include documents in research</p>
                </div>

                <div className="max-h-[320px] overflow-y-auto">
                    {isLoading && (
                        <div className="px-4 py-6 text-center">
                            <div className="text-sm text-neutral-400">Loading knowledge bases...</div>
                        </div>
                    )}

                    {error && (
                        <div className="px-4 py-6 text-center">
                            <div className="text-sm text-red-500">Failed to load knowledge bases</div>
                        </div>
                    )}

                    {!isLoading && !error && knowledgeBases.length === 0 && (
                        <div className="px-4 py-6 text-center">
                            <div className="text-sm text-neutral-500">No knowledge bases found</div>
                            <Link
                                href="/knowledge"
                                className="text-xs text-neutral-600 hover:text-neutral-900 mt-1 inline-block"
                            >
                                Create a knowledge base
                            </Link>
                        </div>
                    )}

                    {!isLoading && !error && knowledgeBases.length > 0 && (
                        <div className="py-1">
                            {knowledgeBases.map((kb) => {
                                const isActive = activeKnowledgeBaseIds.includes(kb.id);
                                return (
                                    <div
                                        key={kb.id}
                                        className="flex items-center justify-between px-4 py-2.5 hover:bg-neutral-50 transition-colors"
                                    >
                                        <div className="min-w-0">
                                            <div className="text-sm font-medium text-neutral-800 truncate">
                                                {kb.name}
                                            </div>
                                            <div className="text-xs text-neutral-400 truncate">
                                                {kb.document_count} document{kb.document_count !== 1 ? 's' : ''}
                                            </div>
                                        </div>
                                        <Switch
                                            checked={isActive}
                                            onCheckedChange={() => onToggleKnowledgeBase(kb.id)}
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
                        href="/knowledge"
                        className="text-xs text-neutral-600 hover:text-neutral-900 font-medium"
                    >
                        + Add knowledge base
                    </Link>
                </div>
            </PopoverContent>
        </Popover>
    );
});
