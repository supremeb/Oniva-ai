// Types for workflow components
// Extracted to avoid circular dependency issues with dynamic imports

export interface TestRunResult {
    status: 'success' | 'failed';
    output?: unknown;
    error?: string;
    elapsedMs: number;
    totalTokens?: number;
    nodeResults?: Array<{
        nodeId: string;
        nodeName: string;
        status: 'success' | 'failed';
        output?: unknown;
        elapsedMs: number;
    }>;
}
