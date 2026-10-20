'use client';

import React from 'react';
import { useWorkflowStore } from '@/lib/stores/workflow';
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip";
import { ScrollArea } from "@/components/ui/scroll-area";

interface NodeInspectorProps {
    id: string;
    children: React.ReactNode;
}

export function NodeInspector({ id, children }: NodeInspectorProps) {
    const nodeStatus = useWorkflowStore((state) => state.nodeStatusMap[id]);
    const executionResult = useWorkflowStore((state) => state.executionResults[id]);

    if (nodeStatus !== 'completed' || !executionResult) {
        return <>{children}</>;
    }

    return (
        <TooltipProvider>
            <Tooltip delayDuration={300}>
                <TooltipTrigger asChild>
                    {children}
                </TooltipTrigger>
                <TooltipContent side="right" className="w-80 p-0 overflow-hidden bg-background border shadow-xl">
                    <div className="bg-muted px-3 py-2 border-b font-medium text-xs flex justify-between items-center">
                        <span>Execution Output</span>
                    </div>
                    <ScrollArea className="h-64 p-3 font-mono text-[10px] bg-black text-green-400">
                        <pre className="whitespace-pre-wrap break-all">
                            {JSON.stringify(executionResult.result, null, 2)}
                        </pre>
                    </ScrollArea>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}
