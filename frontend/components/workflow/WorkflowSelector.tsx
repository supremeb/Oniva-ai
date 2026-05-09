'use client';

import { useState, useEffect, useCallback } from 'react';
import { Check, CaretDown, TreeStructure } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from '@/components/ui/command';
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';
import { listWorkflows } from '@/lib/api/workflows';
import type { Workflow } from '@/types/api';

interface WorkflowSelectorProps {
    value?: string;
    valueName?: string; // Display name for the selected workflow (fallback before load)
    currentWorkflowId?: string; // To exclude current workflow from list
    onChange: (workflowId: string, workflowName: string) => void;
    placeholder?: string;
    disabled?: boolean;
}

export function WorkflowSelector({
    value,
    valueName,
    currentWorkflowId,
    onChange,
    placeholder = 'Select workflow...',
    disabled = false,
}: WorkflowSelectorProps) {
    const [open, setOpen] = useState(false);
    const [workflows, setWorkflows] = useState<Workflow[]>([]);
    const [loading, setLoading] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');

    // Fetch workflows on mount and when popover opens
    useEffect(() => {
        const fetchWorkflows = async () => {
            setLoading(true);
            try {
                const data = await listWorkflows();
                // Filter out current workflow to prevent circular references
                const filtered = currentWorkflowId
                    ? data.filter(w => w.id !== currentWorkflowId)
                    : data;
                setWorkflows(filtered);
            } catch (error) {
                console.error('Failed to fetch workflows:', error);
            } finally {
                setLoading(false);
            }
        };
        fetchWorkflows();
    }, [currentWorkflowId]);

    // Get selected workflow - either from loaded workflows or use the passed name
    const selectedWorkflow = workflows.find(w => w.id === value);
    const displayName = selectedWorkflow?.name || valueName;

    // Filter workflows based on search
    const filteredWorkflows = workflows.filter(w =>
        w.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (w.description && w.description.toLowerCase().includes(searchQuery.toLowerCase()))
    );

    const handleSelect = useCallback((workflowId: string) => {
        const workflow = workflows.find(w => w.id === workflowId);
        if (workflow) {
            onChange(workflow.id, workflow.name);
        }
        setOpen(false);
        setSearchQuery(''); // Reset search on selection
    }, [workflows, onChange]);

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    role="combobox"
                    aria-expanded={open}
                    disabled={disabled}
                    className="w-full justify-between font-normal h-9 px-3"
                >
                    <div className="flex items-center gap-2 truncate flex-1 min-w-0">
                        {value && displayName ? (
                            <>
                                <TreeStructure className="h-4 w-4 shrink-0 text-indigo-500" weight="bold" />
                                <span className="truncate">{displayName}</span>
                            </>
                        ) : (
                            <span className="text-muted-foreground">{placeholder}</span>
                        )}
                    </div>
                    <CaretDown className="h-4 w-4 shrink-0 opacity-50" />
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
                <Command shouldFilter={false}>
                    <CommandInput
                        placeholder="Search workflows..."
                        value={searchQuery}
                        onValueChange={setSearchQuery}
                    />
                    <CommandList className="max-h-[300px]">
                        {loading ? (
                            <div className="py-6 text-center text-sm text-muted-foreground">
                                Loading workflows...
                            </div>
                        ) : filteredWorkflows.length === 0 ? (
                            <CommandEmpty>
                                {searchQuery ? 'No workflows found.' : 'No workflows available.'}
                            </CommandEmpty>
                        ) : (
                            <CommandGroup>
                                {filteredWorkflows.map((workflow) => (
                                    <CommandItem
                                        key={workflow.id}
                                        value={workflow.id}
                                        onSelect={() => handleSelect(workflow.id)}
                                        className="flex items-center gap-2 py-2 cursor-pointer"
                                    >
                                        <Check
                                            className={cn(
                                                "h-4 w-4 shrink-0",
                                                value === workflow.id ? "opacity-100 text-indigo-600" : "opacity-0"
                                            )}
                                            weight="bold"
                                        />
                                        <div className="flex flex-col gap-0.5 min-w-0 flex-1">
                                            <span className="font-medium truncate">{workflow.name}</span>
                                            {workflow.description && (
                                                <span className="text-xs text-muted-foreground truncate">
                                                    {workflow.description}
                                                </span>
                                            )}
                                        </div>
                                    </CommandItem>
                                ))}
                            </CommandGroup>
                        )}
                    </CommandList>
                </Command>
            </PopoverContent>
        </Popover>
    );
}
