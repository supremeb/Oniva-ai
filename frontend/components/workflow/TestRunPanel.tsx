'use client';

import { useMemo, useRef, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { X, Play, CheckCircle, XCircle, CircleNotch, Clock, ArrowRight } from '@phosphor-icons/react';
import type { Node } from '@xyflow/react';
import type { TestRunResult } from './types';
import { WorkflowRunTree } from './WorkflowRunTree';
import { useTestRunWaiting } from '@/hooks/useWorkflowRunTree';
import { overlayWaiting } from '@/lib/run/adapters/workflowRun';
import { liveRunToTree, type LiveRun } from '@/lib/run/workflowStream';

// Re-export for backward compatibility
export type { TestRunResult } from './types';

interface TestRunPanelProps {
    isOpen: boolean;
    onClose: () => void;
    nodes: Node[];
    onRun: (inputs: Record<string, string>) => Promise<TestRunResult>;
    /** The stream's per-node state, updated by the editor while the run goes (plan 003 U26). */
    liveRun?: LiveRun;
    workflowId?: string | null;
}

type TabType = 'input' | 'result' | 'detail' | 'tracing';

export function TestRunPanel({ isOpen, onClose, nodes, onRun, liveRun, workflowId = null }: TestRunPanelProps) {
    const [activeTab, setActiveTab] = useState<TabType>('input');
    const [inputs, setInputs] = useState<Record<string, string>>({});
    const [isRunning, setIsRunning] = useState(false);
    const [result, setResult] = useState<TestRunResult | null>(null);
    const startedAtRef = useRef(0);
    const { waiting, decide } = useTestRunWaiting(workflowId, isRunning, startedAtRef.current);
    const { tree, waiting: waitingByNode } = useMemo(() => {
        const names: Record<string, string> = {};
        for (const n of nodes) {
            const label = (n.data as Record<string, unknown> | undefined)?.label;
            if (typeof label === 'string' && label) names[n.id] = label;
        }
        return overlayWaiting(liveRun ? liveRunToTree(liveRun, names) : [], waiting);
    }, [liveRun, nodes, waiting]);

    if (!isOpen) return null;

    // Extract input variables from start node
    const startNode = nodes.find((n) => n.type === 'start');
    const inputVariables = (startNode?.data as Record<string, unknown>)?.variables as Array<{ name: string; type: string }> || [];

    const handleRun = async () => {
        startedAtRef.current = Date.now();
        setIsRunning(true);
        setActiveTab('result');

        try {
            const runResult = await onRun(inputs);
            setResult(runResult);
        } catch (error) {
            setResult({
                status: 'failed',
                error: error instanceof Error ? error.message : 'Unknown error',
                elapsedMs: 0,
            });
        } finally {
            setIsRunning(false);
        }
    };

    const tabs: { id: TabType; label: string }[] = [
        { id: 'input', label: 'INPUT' },
        { id: 'result', label: 'RESULT' },
        { id: 'detail', label: 'DETAIL' },
        { id: 'tracing', label: 'TRACING' },
    ];

    return (
        <Card className="w-80 h-full border-l rounded-none shadow-lg flex flex-col">
            <CardHeader className="pb-2 border-b">
                <div className="flex items-center justify-between">
                    <CardTitle className="text-sm font-medium">Test Run</CardTitle>
                    <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onClose}>
                        <X className="h-4 w-4" />
                    </Button>
                </div>

                {/* Tabs */}
                <div className="flex gap-1 mt-2">
                    {tabs.map((tab) => (
                        <button
                            key={tab.id}
                            onClick={() => setActiveTab(tab.id)}
                            className={`px-3 py-1.5 text-xs font-medium rounded transition-colors ${activeTab === tab.id
                                ? 'bg-primary text-primary-foreground'
                                : 'text-muted-foreground hover:bg-muted'
                                }`}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>
            </CardHeader>

            <div className="flex-1 overflow-auto">
                <CardContent className="p-4">
                    {activeTab === 'input' && (
                        <div className="space-y-4">
                            {inputVariables.length === 0 ? (
                                <div className="text-center py-8 text-muted-foreground">
                                    <p className="text-sm">No input variables defined</p>
                                    <p className="text-xs mt-1">Add variables to the Start node</p>
                                </div>
                            ) : (
                                inputVariables.map((variable) => (
                                    <div key={variable.name} className="space-y-1.5">
                                        <Label className="text-xs">{variable.name}</Label>
                                        <Input
                                            value={inputs[variable.name] || ''}
                                            onChange={(e) =>
                                                setInputs((prev) => ({ ...prev, [variable.name]: e.target.value }))
                                            }
                                            placeholder={`Enter ${variable.name}...`}
                                            className="text-sm"
                                        />
                                    </div>
                                ))
                            )}

                            {/* sys.query is always available */}
                            <div className="space-y-1.5">
                                <Label className="text-xs">sys.query</Label>
                                <Input
                                    value={inputs['sys.query'] || ''}
                                    onChange={(e) =>
                                        setInputs((prev) => ({ ...prev, 'sys.query': e.target.value }))
                                    }
                                    placeholder="Enter query..."
                                    className="text-sm"
                                />
                            </div>
                        </div>
                    )}

                    {activeTab === 'result' && (
                        <div className="space-y-4">
                            {isRunning ? (
                                <div className="flex flex-col items-center py-8">
                                    <CircleNotch className="h-8 w-8 animate-spin text-primary" />
                                    <p className="text-sm text-muted-foreground mt-2">Running workflow...</p>
                                    {tree.length > 0 && (
                                        <div className="mt-4 w-full">
                                            <WorkflowRunTree tree={tree} waiting={waitingByNode} onDecide={decide} aria-label="Test run" />
                                        </div>
                                    )}
                                </div>
                            ) : result ? (
                                <div className="space-y-4">
                                    <div className={`flex items-center gap-2 p-3 rounded-lg ${result.status === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
                                        }`}>
                                        {result.status === 'success' ? (
                                            <CheckCircle className="h-5 w-5" weight="fill" />
                                        ) : (
                                            <XCircle className="h-5 w-5" weight="fill" />
                                        )}
                                        <span className="text-sm font-medium">
                                            {result.status === 'success' ? 'Completed' : 'Failed'}
                                        </span>
                                    </div>

                                    {result.output !== undefined && (
                                        <div className="space-y-2">
                                            <Label className="text-xs">Output</Label>
                                            <pre className="p-3 bg-muted rounded-lg text-xs overflow-auto max-h-60">
                                                {String(typeof result.output === 'string' ? result.output : JSON.stringify(result.output, null, 2))}
                                            </pre>
                                        </div>
                                    )}

                                    {result.error && (
                                        <div className="space-y-2">
                                            <Label className="text-xs text-red-600">Error</Label>
                                            <pre className="p-3 bg-red-50 text-red-600 rounded-lg text-xs overflow-auto">
                                                {result.error}
                                            </pre>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="text-center py-8 text-muted-foreground">
                                    <Play className="h-8 w-8 mx-auto opacity-50" />
                                    <p className="text-sm mt-2">Run the workflow to see results</p>
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'detail' && (
                        <div className="space-y-4">
                            {result ? (
                                <div className="space-y-3">
                                    <div className="flex justify-between text-sm">
                                        <span className="text-muted-foreground">Status</span>
                                        <span className={result.status === 'success' ? 'text-green-600' : 'text-red-600'}>
                                            {result.status === 'success' ? 'Success' : 'Failed'}
                                        </span>
                                    </div>
                                    <div className="flex justify-between text-sm">
                                        <span className="text-muted-foreground">Elapsed Time</span>
                                        <span>{result.elapsedMs}ms</span>
                                    </div>
                                    {result.totalTokens && (
                                        <div className="flex justify-between text-sm">
                                            <span className="text-muted-foreground">Total Tokens</span>
                                            <span>{result.totalTokens.toLocaleString()}</span>
                                        </div>
                                    )}

                                    <div className="pt-3 border-t">
                                        <Label className="text-xs">Raw Input</Label>
                                        <pre className="mt-2 p-3 bg-muted rounded-lg text-xs overflow-auto">
                                            {JSON.stringify(inputs, null, 2)}
                                        </pre>
                                    </div>
                                </div>
                            ) : (
                                <div className="text-center py-8 text-muted-foreground">
                                    <Clock className="h-8 w-8 mx-auto opacity-50" />
                                    <p className="text-sm mt-2">No execution details yet</p>
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'tracing' && (
                        <div className="space-y-2">
                            {tree.length > 0 && (
                                <WorkflowRunTree tree={tree} waiting={waitingByNode} onDecide={decide} aria-label="Test run" />
                            )}
                            {result?.nodeResults && result.nodeResults.length > 0 ? (
                                result.nodeResults.map((node, index) => (
                                    <div key={node.nodeId} className="flex items-start gap-2">
                                        <div className="flex flex-col items-center">
                                            <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs ${node.status === 'success' ? 'bg-green-100 text-green-600' : 'bg-red-100 text-red-600'
                                                }`}>
                                                {index + 1}
                                            </div>
                                            {index < result.nodeResults!.length - 1 && (
                                                <div className="w-0.5 h-4 bg-border" />
                                            )}
                                        </div>
                                        <div className="flex-1 pb-2">
                                            <div className="flex items-center gap-2">
                                                <span className="text-sm font-medium">{node.nodeName}</span>
                                                <span className="text-xs text-muted-foreground">{node.elapsedMs}ms</span>
                                            </div>
                                            {node.output !== undefined && (
                                                <pre className="mt-1 p-2 bg-muted rounded text-xs overflow-auto max-h-20">
                                                    {String(typeof node.output === 'string' ? node.output : JSON.stringify(node.output, null, 2))}
                                                </pre>
                                            )}
                                        </div>
                                    </div>
                                ))
                            ) : tree.length > 0 ? null : (
                                <div className="text-center py-8 text-muted-foreground">
                                    <ArrowRight className="h-8 w-8 mx-auto opacity-50" />
                                    <p className="text-sm mt-2">Run the workflow to see tracing</p>
                                </div>
                            )}
                        </div>
                    )}
                </CardContent>
            </div>

            <div className="p-4 border-t">
                <Button
                    className="w-full"
                    onClick={handleRun}
                    disabled={isRunning}
                >
                    {isRunning ? (
                        <>
                            <CircleNotch className="h-4 w-4 mr-2 animate-spin" />
                            Running...
                        </>
                    ) : (
                        <>
                            <Play className="h-4 w-4 mr-2" weight="fill" />
                            Start Run
                        </>
                    )}
                </Button>
            </div>
        </Card>
    );
}
