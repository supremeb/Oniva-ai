'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { ShieldCheck, CheckCircle, XCircle } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface ComplianceNodeData {
    label: string;
    checkTypes?: string[];
    inputData?: string;
    riskThreshold?: string;
    failOnViolation?: boolean;
    status?: 'idle' | 'running' | 'complete' | 'error';
    result?: {
        isCompliant?: boolean;
        riskLevel?: 'low' | 'medium' | 'high';
        violations?: string[];
    };
}

const CHECK_TYPE_LABELS: Record<string, string> = {
    sox: 'SOX',
    mifid: 'MiFID II',
    basel: 'Basel III',
    aml: 'AML/KYC',
    gdpr: 'GDPR',
    custom: 'Custom',
};

function ComplianceNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as ComplianceNodeData;
    const hasError = !nodeData.checkTypes?.length;

    const riskConfig: Record<string, { variant: 'success' | 'warning' | 'error' }> = {
        low: { variant: 'success' },
        medium: { variant: 'warning' },
        high: { variant: 'error' },
    };

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="compliance"
            icon={<ShieldCheck className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'Compliance'}
            category="AI"
            description="Regulatory compliance checks"
            hasError={hasError}
        >
            {nodeData.checkTypes && nodeData.checkTypes.length > 0 && (
                <div className="flex flex-wrap gap-1">
                    {nodeData.checkTypes.map((type) => (
                        <NodeBadge key={type} variant="default">
                            {CHECK_TYPE_LABELS[type] || type}
                        </NodeBadge>
                    ))}
                </div>
            )}

            {nodeData.riskThreshold && (
                <NodeInfo
                    label="Threshold"
                    value={<span className="capitalize">{nodeData.riskThreshold}</span>}
                />
            )}

            {nodeData.result?.riskLevel && (
                <div className="flex items-center justify-between">
                    <span className="text-muted-foreground text-[11px] uppercase tracking-wide">Risk Level</span>
                    <NodeBadge variant={riskConfig[nodeData.result.riskLevel].variant}>
                        {nodeData.result.riskLevel.toUpperCase()}
                    </NodeBadge>
                </div>
            )}

            {nodeData.result?.isCompliant !== undefined && (
                <div className={`p-2 rounded-lg flex items-center gap-2 ${nodeData.result.isCompliant ? 'bg-emerald-50 border border-emerald-200' : 'bg-red-50 border border-red-200'}`}>
                    {nodeData.result.isCompliant ? (
                        <>
                            <CheckCircle className="h-4 w-4 text-emerald-600" weight="fill" />
                            <span className="font-semibold text-[11px] text-emerald-700">Compliant</span>
                        </>
                    ) : (
                        <>
                            <XCircle className="h-4 w-4 text-red-600" weight="fill" />
                            <span className="font-semibold text-[11px] text-red-700">Non-Compliant</span>
                        </>
                    )}
                </div>
            )}

            {nodeData.result?.violations && nodeData.result.violations.length > 0 && (
                <div className="p-2 bg-red-50 rounded-lg border border-red-200">
                    <div className="text-red-700 font-semibold text-[10px] mb-1">
                        {nodeData.result.violations.length} Violation{nodeData.result.violations.length > 1 ? 's' : ''}
                    </div>
                    <ul className="list-disc list-inside text-red-600 text-[10px] space-y-0.5">
                        {nodeData.result.violations.slice(0, 2).map((v, i) => (
                            <li key={i} className="line-clamp-1">{v}</li>
                        ))}
                    </ul>
                </div>
            )}
        </BaseNode>
    );
}

export const ComplianceNode = memo(ComplianceNodeComponent);
