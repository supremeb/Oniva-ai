'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { FileText } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface DocumentExtractorNodeData {
    label: string;
    inputDocument: string;
    extractionType: 'full_text' | 'structured' | 'sections' | 'tables' | 'key_value';
    model?: string;
    schemaDefinition?: string;
    outputFormat: 'text' | 'json' | 'markdown';
}

const extractionTypeLabels: Record<string, string> = {
    full_text: 'Full Text',
    structured: 'Structured Data',
    sections: 'By Sections',
    tables: 'Tables Only',
    key_value: 'Key-Value Pairs',
};

function DocumentExtractorNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as DocumentExtractorNodeData;
    const extractionType = nodeData.extractionType || 'full_text';
    const hasError = !nodeData.inputDocument;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="knowledge"
            icon={<FileText className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Document Extractor'}
            category="Transform"
            description={extractionTypeLabels[extractionType] || 'Extract document content'}
            hasError={hasError}
        >
            <NodeInfo
                label="Extraction"
                value={extractionTypeLabels[extractionType] || extractionType}
            />
            <NodeInfo
                label="Input"
                value={nodeData.inputDocument ? `{{${nodeData.inputDocument.replace(/{{|}}/g, '')}}}` : 'Not set'}
            />
            <NodeInfo
                label="Output"
                value={nodeData.outputFormat || 'text'}
            />
            {nodeData.model && (
                <NodeInfo label="Model" value={nodeData.model} />
            )}
        </BaseNode>
    );
}

export const DocumentExtractorNode = memo(DocumentExtractorNodeComponent);
