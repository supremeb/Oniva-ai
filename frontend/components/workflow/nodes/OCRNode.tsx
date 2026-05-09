'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Scan } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface OCRNodeData {
    label: string;
    inputImage: string;
    provider: 'tesseract' | 'google_vision' | 'azure_vision' | 'aws_textract';
    language: string;
    outputFormat: 'text' | 'hocr' | 'json' | 'blocks';
    preprocessImage: boolean;
    detectTables: boolean;
    detectHandwriting: boolean;
}

const providerLabels: Record<string, string> = {
    tesseract: 'Tesseract (Local)',
    google_vision: 'Google Vision',
    azure_vision: 'Azure Vision',
    aws_textract: 'AWS Textract',
};

function OCRNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as OCRNodeData;
    const provider = nodeData.provider || 'tesseract';
    const hasError = !nodeData.inputImage;

    const features = [];
    if (nodeData.preprocessImage) features.push('Preprocess');
    if (nodeData.detectTables) features.push('Tables');
    if (nodeData.detectHandwriting) features.push('Handwriting');

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="data"
            icon={<Scan className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'OCR'}
            category="Transform"
            description={`Extract text via ${providerLabels[provider]?.split(' ')[0] || provider}`}
            hasError={hasError}
        >
            <NodeInfo
                label="Provider"
                value={providerLabels[provider] || provider}
            />
            <NodeInfo
                label="Input"
                value={nodeData.inputImage ? `{{${nodeData.inputImage.replace(/{{|}}/g, '')}}}` : 'Not set'}
            />
            {nodeData.language && (
                <NodeInfo label="Language" value={nodeData.language} />
            )}
            {features.length > 0 && (
                <NodeInfo label="Features" value={features.join(', ')} />
            )}
        </BaseNode>
    );
}

export const OCRNode = memo(OCRNodeComponent);
