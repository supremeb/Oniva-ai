'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { ListBullets } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export type ListOperation =
    | 'first' | 'last' | 'at_index' | 'slice'
    | 'filter' | 'sort' | 'reverse' | 'unique'
    | 'map' | 'reduce' | 'flatten' | 'concat'
    | 'length' | 'includes' | 'find' | 'group_by';

export interface ListOperatorNodeData {
    label: string;
    inputArray: string;
    operation: ListOperation;
    // Operation-specific config
    index?: number;
    startIndex?: number;
    endIndex?: number;
    filterCondition?: string;
    sortKey?: string;
    sortDirection?: 'asc' | 'desc';
    mapExpression?: string;
    reduceExpression?: string;
    groupByKey?: string;
    searchValue?: string;
}

const operationLabels: Record<ListOperation, string> = {
    first: 'Get First',
    last: 'Get Last',
    at_index: 'Get At Index',
    slice: 'Slice',
    filter: 'Filter',
    sort: 'Sort',
    reverse: 'Reverse',
    unique: 'Unique',
    map: 'Map/Transform',
    reduce: 'Reduce',
    flatten: 'Flatten',
    concat: 'Concatenate',
    length: 'Length',
    includes: 'Includes',
    find: 'Find',
    group_by: 'Group By'
};

function ListOperatorNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as ListOperatorNodeData;
    const operation = nodeData.operation || 'first';
    const hasError = !nodeData.inputArray;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="aggregator"
            icon={<ListBullets className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'List Operator'}
            category="Transform"
            description={operationLabels[operation] || 'Process lists'}
            hasError={hasError}
        >
            <NodeInfo
                label="Operation"
                value={operationLabels[operation] || operation}
            />
            <NodeInfo
                label="Input"
                value={nodeData.inputArray ? `{{${nodeData.inputArray.replace(/{{|}}/g, '')}}}` : 'Not set'}
            />
            {(operation === 'at_index' || operation === 'slice') && nodeData.index !== undefined && (
                <NodeInfo label="Index" value={String(nodeData.index)} />
            )}
            {operation === 'sort' && nodeData.sortKey && (
                <NodeInfo label="Sort By" value={nodeData.sortKey} />
            )}
            {operation === 'group_by' && nodeData.groupByKey && (
                <NodeInfo label="Group Key" value={nodeData.groupByKey} />
            )}
        </BaseNode>
    );
}

export const ListOperatorNode = memo(ListOperatorNodeComponent);
