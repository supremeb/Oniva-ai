'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Plus, Trash, X } from '@phosphor-icons/react';
import type { Node } from '@xyflow/react';

export interface Condition {
    id: string;
    variable: string;
    operator: ConditionOperator;
    value: string;
}

export interface ConditionGroup {
    id: string;
    logic: 'and' | 'or';
    conditions: Condition[];
}

export type ConditionOperator =
    | 'equals'
    | 'not_equals'
    | 'contains'
    | 'not_contains'
    | 'starts_with'
    | 'ends_with'
    | 'is_empty'
    | 'is_not_empty'
    | 'greater_than'
    | 'less_than'
    | 'greater_equal'
    | 'less_equal';

const OPERATORS: { value: ConditionOperator; label: string }[] = [
    { value: 'equals', label: 'Equals' },
    { value: 'not_equals', label: 'Not equals' },
    { value: 'contains', label: 'Contains' },
    { value: 'not_contains', label: 'Does not contain' },
    { value: 'starts_with', label: 'Starts with' },
    { value: 'ends_with', label: 'Ends with' },
    { value: 'is_empty', label: 'Is empty' },
    { value: 'is_not_empty', label: 'Is not empty' },
    { value: 'greater_than', label: 'Greater than' },
    { value: 'less_than', label: 'Less than' },
    { value: 'greater_equal', label: 'Greater or equal' },
    { value: 'less_equal', label: 'Less or equal' },
];

const NO_VALUE_OPERATORS = ['is_empty', 'is_not_empty'];

interface ConditionBuilderProps {
    conditions: ConditionGroup[];
    onChange: (conditions: ConditionGroup[]) => void;
    nodes: Node[];
    currentNodeId: string;
}

function getAvailableVariables(nodes: Node[], currentNodeId: string) {
    const currentIndex = nodes.findIndex((n) => n.id === currentNodeId);
    const precedingNodes = nodes.slice(0, currentIndex);

    return precedingNodes.flatMap((node) => {
        const nodeData = node.data as Record<string, unknown>;
        const label = (nodeData.label as string) || node.type || 'Node';
        const outputs: string[] = [];

        // Default outputs based on node type
        switch (node.type) {
            case 'start':
                outputs.push('query');
                break;
            case 'llm':
                outputs.push('text', 'message');
                break;
            case 'knowledge':
                outputs.push('result');
                break;
            case 'code':
                outputs.push('result');
                break;
            case 'http':
                outputs.push('body', 'status');
                break;
            case 'template':
                outputs.push('output');
                break;
            default:
                outputs.push('output');
        }

        return outputs.map((output) => ({
            nodeId: node.id,
            nodeLabel: label,
            variable: output,
            fullPath: `${node.id}.${output}`,
        }));
    });
}

export function ConditionBuilder({ conditions, onChange, nodes, currentNodeId }: ConditionBuilderProps) {
    const [expandedVarPicker, setExpandedVarPicker] = useState<string | null>(null);
    const availableVariables = getAvailableVariables(nodes, currentNodeId);

    const generateId = () => `cond_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;

    const addConditionGroup = () => {
        onChange([
            ...conditions,
            {
                id: generateId(),
                logic: 'and',
                conditions: [{ id: generateId(), variable: '', operator: 'equals', value: '' }],
            },
        ]);
    };

    const removeConditionGroup = (groupId: string) => {
        onChange(conditions.filter((g) => g.id !== groupId));
    };

    const updateGroupLogic = (groupId: string, logic: 'and' | 'or') => {
        onChange(
            conditions.map((g) => (g.id === groupId ? { ...g, logic } : g))
        );
    };

    const addConditionToGroup = (groupId: string) => {
        onChange(
            conditions.map((g) =>
                g.id === groupId
                    ? {
                        ...g,
                        conditions: [
                            ...g.conditions,
                            { id: generateId(), variable: '', operator: 'equals' as ConditionOperator, value: '' },
                        ],
                    }
                    : g
            )
        );
    };

    const removeCondition = (groupId: string, conditionId: string) => {
        onChange(
            conditions.map((g) =>
                g.id === groupId
                    ? { ...g, conditions: g.conditions.filter((c) => c.id !== conditionId) }
                    : g
            )
        );
    };

    const updateCondition = (
        groupId: string,
        conditionId: string,
        field: keyof Condition,
        value: string
    ) => {
        onChange(
            conditions.map((g) =>
                g.id === groupId
                    ? {
                        ...g,
                        conditions: g.conditions.map((c) =>
                            c.id === conditionId ? { ...c, [field]: value } : c
                        ),
                    }
                    : g
            )
        );
    };

    // Initialize with one empty condition group if none exist
    if (conditions.length === 0) {
        return (
            <div className="space-y-3">
                <Label className="text-xs text-muted-foreground">No conditions defined</Label>
                <Button size="sm" variant="outline" onClick={addConditionGroup} className="w-full">
                    <Plus className="h-3 w-3 mr-1" />
                    Add Condition
                </Button>
            </div>
        );
    }

    return (
        <div className="space-y-3">
            {conditions.map((group, groupIndex) => (
                <div key={group.id} className="space-y-2 p-3 border rounded-lg bg-muted/30">
                    {/* Group header */}
                    <div className="flex items-center justify-between">
                        <span className="text-xs font-medium text-muted-foreground">
                            {groupIndex === 0 ? 'IF' : 'ELIF'}
                        </span>
                        {conditions.length > 1 && (
                            <Button
                                size="sm"
                                variant="ghost"
                                className="h-5 w-5 p-0"
                                onClick={() => removeConditionGroup(group.id)}
                            >
                                <Trash className="h-3 w-3 text-red-500" />
                            </Button>
                        )}
                    </div>

                    {/* Conditions */}
                    {group.conditions.map((condition, condIndex) => (
                        <div key={condition.id} className="space-y-2">
                            {condIndex > 0 && (
                                <div className="flex items-center gap-2">
                                    <div className="flex-1 h-px bg-border" />
                                    <select
                                        className="h-6 px-2 text-xs rounded border bg-background"
                                        value={group.logic}
                                        onChange={(e) =>
                                            updateGroupLogic(group.id, e.target.value as 'and' | 'or')
                                        }
                                    >
                                        <option value="and">AND</option>
                                        <option value="or">OR</option>
                                    </select>
                                    <div className="flex-1 h-px bg-border" />
                                </div>
                            )}

                            <div className="flex items-center gap-2">
                                {/* Variable picker */}
                                <div className="relative flex-1">
                                    <Input
                                        className="h-8 text-xs"
                                        placeholder="Select variable..."
                                        value={condition.variable}
                                        onFocus={() => setExpandedVarPicker(condition.id)}
                                        onChange={(e) =>
                                            updateCondition(group.id, condition.id, 'variable', e.target.value)
                                        }
                                    />
                                    {expandedVarPicker === condition.id && (
                                        <div className="absolute z-10 top-full left-0 right-0 mt-1 p-2 bg-background border rounded-md shadow-lg max-h-40 overflow-y-auto">
                                            <div className="flex justify-between items-center mb-2">
                                                <span className="text-xs font-medium">Variables</span>
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    className="h-4 w-4 p-0"
                                                    onClick={() => setExpandedVarPicker(null)}
                                                >
                                                    <X className="h-3 w-3" />
                                                </Button>
                                            </div>
                                            {availableVariables.map((v) => (
                                                <button
                                                    key={v.fullPath}
                                                    className="w-full text-left px-2 py-1 text-xs hover:bg-muted rounded flex items-center gap-2"
                                                    onClick={() => {
                                                        updateCondition(
                                                            group.id,
                                                            condition.id,
                                                            'variable',
                                                            v.fullPath
                                                        );
                                                        setExpandedVarPicker(null);
                                                    }}
                                                >
                                                    <span className="text-muted-foreground">{v.nodeLabel}/</span>
                                                    <span>{v.variable}</span>
                                                </button>
                                            ))}
                                            {availableVariables.length === 0 && (
                                                <div className="text-xs text-muted-foreground p-2">
                                                    No variables available
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>

                                {/* Operator */}
                                <select
                                    className="h-8 px-2 text-xs rounded border bg-background min-w-[120px]"
                                    value={condition.operator}
                                    onChange={(e) =>
                                        updateCondition(
                                            group.id,
                                            condition.id,
                                            'operator',
                                            e.target.value
                                        )
                                    }
                                >
                                    {OPERATORS.map((op) => (
                                        <option key={op.value} value={op.value}>
                                            {op.label}
                                        </option>
                                    ))}
                                </select>

                                {/* Value (hidden for is_empty/is_not_empty) */}
                                {!NO_VALUE_OPERATORS.includes(condition.operator) && (
                                    <Input
                                        className="h-8 text-xs flex-1"
                                        placeholder="Value"
                                        value={condition.value}
                                        onChange={(e) =>
                                            updateCondition(group.id, condition.id, 'value', e.target.value)
                                        }
                                    />
                                )}

                                {/* Remove condition */}
                                {group.conditions.length > 1 && (
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        className="h-8 w-8 p-0"
                                        onClick={() => removeCondition(group.id, condition.id)}
                                    >
                                        <Trash className="h-3 w-3 text-muted-foreground" />
                                    </Button>
                                )}
                            </div>
                        </div>
                    ))}

                    {/* Add condition button */}
                    <Button
                        size="sm"
                        variant="ghost"
                        className="w-full h-7 text-xs"
                        onClick={() => addConditionToGroup(group.id)}
                    >
                        <Plus className="h-3 w-3 mr-1" />
                        Add condition
                    </Button>
                </div>
            ))}

            {/* Add ELIF */}
            <Button size="sm" variant="outline" onClick={addConditionGroup} className="w-full">
                <Plus className="h-3 w-3 mr-1" />
                Add ELIF branch
            </Button>
        </div>
    );
}
