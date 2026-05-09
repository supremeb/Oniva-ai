'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { ChartLineUp, TrendUp, TrendDown, Minus, Clock } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface AnalysisNodeData {
    label: string;
    ticker?: string;
    analysisTypes?: string[];
    analysisType?: 'fundamental' | 'technical' | 'sentiment';
    timePeriod?: string;
    generateRecommendation?: boolean;
    status?: 'idle' | 'running' | 'complete' | 'error';
    result?: {
        recommendation?: 'buy' | 'sell' | 'hold';
        confidence?: number;
    };
}

const ANALYSIS_TYPE_LABELS: Record<string, string> = {
    fundamental: 'Fundamental',
    technical: 'Technical',
    sentiment: 'Sentiment',
    quantitative: 'Quantitative',
};

function AnalysisNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as AnalysisNodeData;
    const hasError = !nodeData.ticker && !nodeData.analysisTypes?.length;

    const recommendationConfig: Record<string, { icon: typeof TrendUp; color: string; bg: string }> = {
        buy: { icon: TrendUp, color: 'text-emerald-600', bg: 'bg-emerald-50 border border-emerald-200' },
        sell: { icon: TrendDown, color: 'text-red-600', bg: 'bg-red-50 border border-red-200' },
        hold: { icon: Minus, color: 'text-amber-600', bg: 'bg-amber-50 border border-amber-200' },
    };

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="analysis"
            icon={<ChartLineUp className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'Analysis'}
            category="AI"
            description={nodeData.ticker ? `Analyze ${nodeData.ticker}` : 'Financial analysis'}
            hasError={hasError}
        >
            {nodeData.ticker && (
                <NodeInfo
                    label="Ticker"
                    value={<span className="font-bold text-lime-700">{nodeData.ticker}</span>}
                />
            )}

            {nodeData.analysisTypes && nodeData.analysisTypes.length > 0 && (
                <div className="flex flex-wrap gap-1">
                    {nodeData.analysisTypes.map((type) => (
                        <NodeBadge key={type} variant="success">
                            {ANALYSIS_TYPE_LABELS[type] || type}
                        </NodeBadge>
                    ))}
                </div>
            )}

            {nodeData.timePeriod && (
                <NodeBadge variant="default">
                    <Clock className="h-3 w-3" />
                    {nodeData.timePeriod}
                </NodeBadge>
            )}

            {nodeData.result?.recommendation && (
                <div className={`p-2 rounded-lg flex items-center justify-between ${recommendationConfig[nodeData.result.recommendation].bg}`}>
                    <div className="flex items-center gap-1.5">
                        {(() => {
                            const RecIcon = recommendationConfig[nodeData.result.recommendation!].icon;
                            return <RecIcon className={`h-4 w-4 ${recommendationConfig[nodeData.result.recommendation!].color}`} weight="bold" />;
                        })()}
                        <span className={`font-bold text-[11px] ${recommendationConfig[nodeData.result.recommendation].color}`}>
                            {nodeData.result.recommendation.toUpperCase()}
                        </span>
                    </div>
                    {nodeData.result.confidence !== undefined && (
                        <span className="text-[10px] text-muted-foreground">
                            {(nodeData.result.confidence * 100).toFixed(0)}%
                        </span>
                    )}
                </div>
            )}
        </BaseNode>
    );
}

export const AnalysisNode = memo(AnalysisNodeComponent);
