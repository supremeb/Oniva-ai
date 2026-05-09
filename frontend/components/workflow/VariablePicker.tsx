'use client';

import { useState, useEffect, useRef } from 'react';
import { MagnifyingGlass, X, Hash, TextT, FileText, ListBullets, Cube } from '@phosphor-icons/react';
import { type Node } from '@xyflow/react';

export interface WorkflowVariable {
    nodeId: string;
    nodeName: string;
    nodeType: string;
    variableName: string;
    variableType: 'string' | 'number' | 'file' | 'array' | 'object';
}

interface VariablePickerProps {
    isOpen: boolean;
    onClose: () => void;
    onSelect: (variable: WorkflowVariable) => void;
    nodes: Node[];
    currentNodeId?: string;
    position?: { x: number; y: number };
    searchQuery?: string;
}

const typeIcons: Record<string, React.ElementType> = {
    string: TextT,
    number: Hash,
    file: FileText,
    array: ListBullets,
    object: Cube,
};

const typeLabels: Record<string, string> = {
    string: 'String',
    number: 'Number',
    file: 'File',
    array: 'Array',
    object: 'Object',
};

// Extract variables from node data based on node type
function extractVariablesFromNode(node: Node): WorkflowVariable[] {
    const variables: WorkflowVariable[] = [];
    const data = node.data as Record<string, unknown>;
    const nodeName = (data.label as string) || node.type || 'Node';
    const nodeType = node.type || 'unknown';

    switch (nodeType) {
        case 'start':
            // Start node outputs user-defined variables
            const startVars = (data.variables as Array<{ name: string; type: string }>) || [];
            startVars.forEach((v) => {
                variables.push({
                    nodeId: node.id,
                    nodeName,
                    nodeType,
                    variableName: v.name,
                    variableType: (v.type as WorkflowVariable['variableType']) || 'string',
                });
            });
            // Also add default 'input' variable
            variables.push({
                nodeId: node.id,
                nodeName,
                nodeType,
                variableName: 'sys.query',
                variableType: 'string',
            });
            break;

        case 'llm':
            // LLM outputs text
            variables.push({
                nodeId: node.id,
                nodeName,
                nodeType,
                variableName: 'text',
                variableType: 'string',
            });
            break;

        case 'knowledge':
            // Knowledge retrieval outputs results array
            variables.push({
                nodeId: node.id,
                nodeName,
                nodeType,
                variableName: 'result',
                variableType: 'array',
            });
            break;

        case 'code':
            // Code node outputs defined variables
            const codeOutputs = (data.outputVariables as string[]) || ['result'];
            codeOutputs.forEach((v) => {
                variables.push({
                    nodeId: node.id,
                    nodeName,
                    nodeType,
                    variableName: v,
                    variableType: 'object',
                });
            });
            break;

        case 'http':
            // HTTP outputs response body and status
            variables.push(
                { nodeId: node.id, nodeName, nodeType, variableName: 'body', variableType: 'object' },
                { nodeId: node.id, nodeName, nodeType, variableName: 'status', variableType: 'number' },
                { nodeId: node.id, nodeName, nodeType, variableName: 'headers', variableType: 'object' }
            );
            break;

        case 'template':
            // Template outputs text
            variables.push({
                nodeId: node.id,
                nodeName,
                nodeType,
                variableName: 'output',
                variableType: 'string',
            });
            break;

        case 'iteration':
            // Iteration outputs array
            variables.push(
                { nodeId: node.id, nodeName, nodeType, variableName: 'item', variableType: 'object' },
                { nodeId: node.id, nodeName, nodeType, variableName: 'index', variableType: 'number' }
            );
            break;

        case 'aggregator':
            // Aggregator outputs combined result
            variables.push({
                nodeId: node.id,
                nodeName,
                nodeType,
                variableName: (data.outputVariable as string) || 'result',
                variableType: 'string',
            });
            break;

        case 'classifier':
            // Classifier outputs class name
            variables.push({
                nodeId: node.id,
                nodeName,
                nodeType,
                variableName: 'class_name',
                variableType: 'string',
            });
            break;
    }

    return variables;
}

// System variables
const systemVariables: WorkflowVariable[] = [
    { nodeId: 'sys', nodeName: 'System', nodeType: 'system', variableName: 'user_id', variableType: 'string' },
    { nodeId: 'sys', nodeName: 'System', nodeType: 'system', variableName: 'timestamp', variableType: 'string' },
    { nodeId: 'sys', nodeName: 'System', nodeType: 'system', variableName: 'conversation_id', variableType: 'string' },
];

export function VariablePicker({
    isOpen,
    onClose,
    onSelect,
    nodes,
    currentNodeId,
    position,
    searchQuery: initialSearch = '',
}: VariablePickerProps) {
    const [search, setSearch] = useState(initialSearch);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (isOpen && inputRef.current) {
            inputRef.current.focus();
        }
    }, [isOpen]);

    useEffect(() => {
        setSearch(initialSearch);
    }, [initialSearch]);

    if (!isOpen) return null;

    // Collect all variables from upstream nodes
    const allVariables: WorkflowVariable[] = [];

    nodes.forEach((node) => {
        // Exclude current node and output node
        if (node.id === currentNodeId || node.type === 'output') return;

        const nodeVars = extractVariablesFromNode(node);
        allVariables.push(...nodeVars);
    });

    // Add system variables
    allVariables.push(...systemVariables);

    // Filter by search
    const filteredVariables = allVariables.filter((v) => {
        const searchLower = search.toLowerCase();
        return (
            v.variableName.toLowerCase().includes(searchLower) ||
            v.nodeName.toLowerCase().includes(searchLower)
        );
    });

    // Group by node
    const grouped = filteredVariables.reduce((acc, v) => {
        if (!acc[v.nodeName]) {
            acc[v.nodeName] = [];
        }
        acc[v.nodeName].push(v);
        return acc;
    }, {} as Record<string, WorkflowVariable[]>);

    const style = position
        ? { position: 'fixed' as const, left: position.x, top: position.y }
        : {};

    return (
        <div
            className="w-64 bg-background border rounded-lg shadow-lg z-50 overflow-hidden"
            style={style}
        >
            <div className="p-2 border-b">
                <div className="relative">
                    <MagnifyingGlass className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <input
                        ref={inputRef}
                        type="text"
                        placeholder="Search variable..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="w-full pl-8 pr-8 py-1.5 text-sm border rounded bg-background"
                        onKeyDown={(e) => {
                            if (e.key === 'Escape') onClose();
                        }}
                    />
                    {search && (
                        <button
                            onClick={() => setSearch('')}
                            className="absolute right-2 top-1/2 -translate-y-1/2"
                        >
                            <X className="h-4 w-4 text-muted-foreground hover:text-foreground" />
                        </button>
                    )}
                </div>
            </div>

            <div className="max-h-64 overflow-y-auto">
                {Object.keys(grouped).length === 0 ? (
                    <div className="p-4 text-sm text-muted-foreground text-center">
                        No variables found
                    </div>
                ) : (
                    Object.entries(grouped).map(([nodeName, variables]) => (
                        <div key={nodeName}>
                            <div className="px-3 py-1.5 text-[10px] font-medium text-muted-foreground uppercase bg-muted/50">
                                {nodeName}
                            </div>
                            {variables.map((v, i) => {
                                const Icon = typeIcons[v.variableType] || TextT;
                                return (
                                    <button
                                        key={`${v.nodeId}-${v.variableName}-${i}`}
                                        className="w-full px-3 py-2 flex items-center gap-2 hover:bg-muted text-left text-sm"
                                        onClick={() => {
                                            onSelect(v);
                                            onClose();
                                        }}
                                    >
                                        <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
                                        <span className="font-medium truncate">{v.variableName}</span>
                                        <span className="text-[10px] text-muted-foreground ml-auto">
                                            {typeLabels[v.variableType]}
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}

// Helper to format variable reference for display
export function formatVariableReference(variable: WorkflowVariable): string {
    return `{{${variable.nodeName}.${variable.variableName}}}`;
}

// Variable pill component for inline display
export function VariablePill({
    variable,
    onRemove
}: {
    variable: WorkflowVariable;
    onRemove?: () => void;
}) {
    return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
            <span>{variable.nodeName} / {variable.variableName}</span>
            {onRemove && (
                <button onClick={onRemove} className="hover:bg-blue-200 rounded">
                    <X className="h-3 w-3" />
                </button>
            )}
        </span>
    );
}
