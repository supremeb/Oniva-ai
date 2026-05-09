// Dify-compatible node exports
export { StartNode } from './StartNode';
export { LLMNode } from './LLMNode';
export { OutputNode } from './OutputNode';
export { KnowledgeNode } from './KnowledgeNode';
export { CodeNode } from './CodeNode';
export { HttpRequestNode } from './HttpRequestNode';
export { IfElseNode } from './IfElseNode';
export { IterationNode } from './IterationNode';
export { TemplateNode } from './TemplateNode';
export { VariableAggregatorNode } from './VariableAggregatorNode';
export { QuestionClassifierNode } from './QuestionClassifierNode';

// New node types for skills, connectors, and agents
export { SkillNode } from './SkillNode';
export { ConnectorNode } from './ConnectorNode';
export { AgentNode } from './AgentNode';

// Legacy financial nodes (deprecated, kept for compatibility)
export { DataNode } from './DataNode';
export { AnalysisNode } from './AnalysisNode';
export { ComplianceNode } from './ComplianceNode';
export { ResearchNode } from './ResearchNode';

// Phase 3: Essential workflow nodes
export { WaitNode } from './WaitNode';
export { VariableAssignerNode } from './VariableAssignerNode';
export { ListOperatorNode } from './ListOperatorNode';
export { NotesNode } from './NotesNode';
export { DocumentExtractorNode } from './DocumentExtractorNode';
export { ParameterExtractorNode } from './ParameterExtractorNode';
export { OCRNode } from './OCRNode';
export { NotificationNode } from './NotificationNode';
export { WebhookTriggerNode } from './WebhookTriggerNode';
export { SubWorkflowNode } from './SubWorkflowNode';
export { HumanInLoopNode } from './HumanInLoopNode';

// Phase 2: Workflow scheduling nodes
export { ScheduleTriggerNode } from './ScheduleTriggerNode';

// Phase 3: Spreadsheet processing nodes
export { SpreadsheetNode } from './SpreadsheetNode';
