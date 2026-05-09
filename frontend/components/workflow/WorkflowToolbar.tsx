'use client';

import { Button } from '@/components/ui/button';
import { Play, CircleNotch, ArrowCounterClockwise } from '@phosphor-icons/react';

interface WorkflowToolbarProps {
    onExecute: () => void;
    onReset?: () => void;
    isExecuting: boolean;
}

export function WorkflowToolbar({
    onExecute,
    onReset,
    isExecuting,
}: WorkflowToolbarProps) {
    return (
        <div className="absolute top-4 right-4 z-10 flex gap-2">
            {onReset && (
                <Button variant="outline" size="sm" onClick={onReset} disabled={isExecuting}>
                    <ArrowCounterClockwise className="h-4 w-4 mr-2" weight="bold" />
                    Reset
                </Button>
            )}

            <Button size="sm" onClick={onExecute} disabled={isExecuting}>
                {isExecuting ? (
                    <CircleNotch className="h-4 w-4 mr-2 animate-spin" weight="bold" />
                ) : (
                    <Play className="h-4 w-4 mr-2" weight="fill" />
                )}
                {isExecuting ? 'Running...' : 'Execute'}
            </Button>
        </div>
    );
}
