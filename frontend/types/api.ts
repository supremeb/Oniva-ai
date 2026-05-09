// API Key types
export interface APIKey {
    id: string;
    name: string;
    provider: string;
    masked_key: string;
    is_default: boolean;
    created_at: string;
    updated_at: string;
}

export interface CreateAPIKeyRequest {
    name: string;
    provider: 'openrouter' | 'openai' | 'anthropic' | 'google' | 'parallelai' | 'exa' | 'perplexity' | 'tavily' | 'e2b';
    api_key: string;
    is_default?: boolean;
}

// Connector types
export interface Connector {
    id: string;
    name: string;
    type: 'mcp' | 'rest' | 'graphql';
    config: Record<string, unknown>;
    is_active: boolean;
    created_at: string;
    updated_at: string;
}

export interface CreateConnectorRequest {
    name: string;
    type: 'mcp' | 'rest' | 'graphql';
    config: Record<string, unknown>;
    is_active?: boolean;
}

// Workflow types
export type WorkflowNodeType =
    // Core flow nodes
    | 'start'
    | 'output'
    // AI/LLM nodes
    | 'llm'
    | 'agent'
    // Integration nodes
    | 'skill'
    | 'connector'
    | 'knowledge'
    | 'http'
    // Logic nodes
    | 'code'
    | 'ifelse'
    | 'iteration'
    | 'template'
    | 'aggregator'
    | 'classifier'
    // Legacy financial nodes (deprecated but supported)
    | 'data'
    | 'analysis'
    | 'compliance'
    | 'research';

export interface WorkflowNode {
    id: string;
    type: WorkflowNodeType;
    position: { x: number; y: number };
    data: Record<string, unknown>;
}

export interface WorkflowEdge {
    id: string;
    source: string;
    target: string;
    sourceHandle?: string;  // For nodes with multiple outputs (ifelse, classifier)
    targetHandle?: string;  // For nodes with multiple inputs
}

export interface WorkflowSpec {
    id?: string;
    name?: string;
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
}

export interface Workflow {
    id: string;
    name: string;
    description?: string;
    spec: WorkflowSpec;
    created_at: string;
    updated_at: string;
}

export interface WorkflowResult {
    status: 'success' | 'error';
    results: Record<string, unknown>;
    execution_time?: number;
}

// Agent types
export interface Agent {
    id: string;
    name: string;
    description: string;
    tools: string[];
    model: string;
}
