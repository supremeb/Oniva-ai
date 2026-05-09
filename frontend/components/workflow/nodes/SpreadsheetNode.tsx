'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Table, FileCsv, FileXls, FunnelSimple, ArrowsDownUp, Calculator, Columns } from '@phosphor-icons/react';
import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface SpreadsheetOperation {
    type: 'filter' | 'select' | 'rename' | 'sort' | 'limit' | 'groupby' | 'pivot' | 'add_column' | 'drop_columns' | 'drop_duplicates' | 'fillna' | 'cast' | 'merge';
    column?: string;
    columns?: string[];
    operator?: string;
    value?: unknown;
    mapping?: Record<string, string>;
    ascending?: boolean;
    n?: number;
    offset?: number;
    aggColumn?: string;
    aggFunc?: 'sum' | 'mean' | 'count' | 'min' | 'max';
    index?: string;
    values?: string;
    name?: string;
    expression?: string;
    subset?: string[];
    keep?: 'first' | 'last' | false;
    method?: 'ffill' | 'bfill';
    dtype?: 'string' | 'int' | 'float' | 'datetime';
    rightData?: string;
    on?: string;
    how?: 'left' | 'right' | 'inner' | 'outer';
}

export interface SpreadsheetNodeData {
    label: string;
    filePath?: string;
    fileContent?: string;
    inputData?: string;
    fileType: 'auto' | 'csv' | 'xlsx';
    sheetName?: string;
    operations: SpreadsheetOperation[];
    outputFormat: 'json' | 'csv' | 'markdown' | 'html' | 'dict' | 'records' | 'columns';
}

const operationLabels: Record<string, string> = {
    filter: 'Filter',
    select: 'Select Columns',
    rename: 'Rename',
    sort: 'Sort',
    limit: 'Limit',
    groupby: 'Group By',
    pivot: 'Pivot',
    add_column: 'Add Column',
    drop_columns: 'Drop Columns',
    drop_duplicates: 'Remove Duplicates',
    fillna: 'Fill Missing',
    cast: 'Type Cast',
    merge: 'Merge',
};

const fileTypeIcons: Record<string, React.ReactNode> = {
    auto: <Table className="h-3.5 w-3.5 text-white" weight="bold" />,
    csv: <FileCsv className="h-3.5 w-3.5 text-white" weight="bold" />,
    xlsx: <FileXls className="h-3.5 w-3.5 text-white" weight="bold" />,
};

const outputFormatLabels: Record<string, string> = {
    json: 'JSON',
    csv: 'CSV',
    markdown: 'Markdown Table',
    html: 'HTML',
    dict: 'Dictionary',
    records: 'Records',
    columns: 'Columns',
};

/**
 * Get a summary of operations
 */
function getOperationsSummary(operations: SpreadsheetOperation[]): string {
    if (!operations || operations.length === 0) {
        return 'No operations';
    }

    if (operations.length === 1) {
        return operationLabels[operations[0].type] || operations[0].type;
    }

    if (operations.length <= 3) {
        return operations.map(op => operationLabels[op.type] || op.type).join(', ');
    }

    return `${operations.length} operations`;
}

/**
 * Get icon for operation type
 */
function getOperationIcon(type: string): React.ReactNode {
    switch (type) {
        case 'filter':
            return <FunnelSimple className="h-3 w-3" weight="bold" />;
        case 'sort':
            return <ArrowsDownUp className="h-3 w-3" weight="bold" />;
        case 'add_column':
        case 'groupby':
            return <Calculator className="h-3 w-3" weight="bold" />;
        case 'select':
        case 'drop_columns':
            return <Columns className="h-3 w-3" weight="bold" />;
        default:
            return null;
    }
}

/**
 * Get data source description
 */
function getDataSource(data: SpreadsheetNodeData): string {
    if (data.filePath) {
        const parts = data.filePath.split('/');
        return parts[parts.length - 1] || data.filePath;
    }
    if (data.inputData) {
        return data.inputData.replace(/\{\{|\}\}/g, '').trim();
    }
    if (data.fileContent) {
        return 'Uploaded file';
    }
    return 'Not configured';
}

function SpreadsheetNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as SpreadsheetNodeData;
    const fileType = nodeData.fileType || 'auto';
    const operations = nodeData.operations || [];
    const outputFormat = nodeData.outputFormat || 'json';

    const hasError = !nodeData.filePath && !nodeData.fileContent && !nodeData.inputData;
    const dataSource = getDataSource(nodeData);
    const operationsSummary = getOperationsSummary(operations);

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="spreadsheet"
            icon={fileTypeIcons[fileType] || fileTypeIcons.auto}
            title={nodeData.label || 'Spreadsheet'}
            category="Transform"
            description="Excel/CSV processing"
            hasError={hasError}
            hasInput={true}
            hasOutput={true}
        >
            <NodeInfo
                label="Source"
                value={
                    <span className="truncate max-w-[120px]" title={dataSource}>
                        {dataSource}
                    </span>
                }
            />
            {nodeData.sheetName && (
                <NodeInfo
                    label="Sheet"
                    value={nodeData.sheetName}
                />
            )}
            <NodeInfo
                label="Type"
                value={<NodeBadge variant="default">{fileType.toUpperCase()}</NodeBadge>}
            />
            <NodeInfo
                label="Operations"
                value={
                    operations.length > 0 ? (
                        <span className="flex items-center gap-1">
                            {operations.length <= 2 && operations.slice(0, 2).map((op, i) => (
                                <span key={i} className="flex items-center gap-0.5 text-muted-foreground">
                                    {getOperationIcon(op.type)}
                                </span>
                            ))}
                            <span className="truncate">{operationsSummary}</span>
                        </span>
                    ) : (
                        <span className="text-muted-foreground">None</span>
                    )
                }
            />
            <NodeInfo
                label="Output"
                value={<NodeBadge variant="info">{outputFormatLabels[outputFormat]}</NodeBadge>}
            />
        </BaseNode>
    );
}

export const SpreadsheetNode = memo(SpreadsheetNodeComponent);
