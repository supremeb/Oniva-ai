'use client';

import { Button } from '@/components/ui/button';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
    DropdownMenuSeparator,
    DropdownMenuLabel,
    DropdownMenuSub,
    DropdownMenuSubContent,
    DropdownMenuSubTrigger,
} from '@/components/ui/dropdown-menu';
import {
    CaretDown,
    Lightning,
    Code,
    GitBranch,
    Database,
    Robot,
    ChatTeardropText,
    MagnifyingGlass,
    ShieldCheck,
    FunnelSimple,
    ArrowsClockwise,
    TreeStructure,
    Scales,
    Globe,
    FileText,
    Users,
    BookOpen,
} from '@phosphor-icons/react';
import type { Icon } from '@phosphor-icons/react';
import type { Node, Edge } from '@xyflow/react';

// Example workflows embedded in frontend
const EXAMPLE_WORKFLOWS = {
    simple_qa: {
        name: 'Simple Q&A',
        description: 'Basic question-answering with LLM',
        icon: Lightning,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: { label: 'Start', variables: [{ name: 'query', type: 'string' }] },
                position: { x: 100, y: 200 },
            },
            {
                id: 'llm-1',
                type: 'llm',
                data: {
                    label: 'Generate Answer',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'You are a helpful AI assistant. Answer questions clearly and concisely.',
                    userPrompt: '{{sys.query}}',
                    temperature: 0.7,
                    maxTokens: 1000,
                },
                position: { x: 350, y: 200 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: { label: 'Output', outputName: 'answer', outputValue: '{{llm-1.text}}' },
                position: { x: 600, y: 200 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'llm-1' },
            { id: 'e2', source: 'llm-1', target: 'output-1' },
        ],
    },
    code_generation: {
        name: 'Code Generation',
        description: 'Generate and review code',
        icon: Code,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: { label: 'Start', variables: [{ name: 'task', type: 'string' }] },
                position: { x: 100, y: 200 },
            },
            {
                id: 'llm-gen',
                type: 'llm',
                data: {
                    label: 'Generate Code',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'You are an expert programmer. Generate clean, well-documented code.',
                    userPrompt: 'Write Python code for: {{sys.query}}\n\nProvide only the code.',
                    temperature: 0.3,
                    maxTokens: 2000,
                },
                position: { x: 350, y: 150 },
            },
            {
                id: 'llm-review',
                type: 'llm',
                data: {
                    label: 'Code Review',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'You are a code reviewer. Analyze for bugs and improvements.',
                    userPrompt: 'Review this code:\n\n{{llm-gen.text}}\n\nProvide:\n1. Issues\n2. Suggestions\n3. Rating (1-10)',
                    temperature: 0.5,
                    maxTokens: 1000,
                },
                position: { x: 600, y: 150 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: { label: 'Output' },
                position: { x: 850, y: 200 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'llm-gen' },
            { id: 'e2', source: 'llm-gen', target: 'llm-review' },
            { id: 'e3', source: 'llm-review', target: 'output-1' },
        ],
    },
    conditional: {
        name: 'Conditional Response',
        description: 'Route based on intent classification',
        icon: GitBranch,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: { label: 'Start', variables: [{ name: 'message', type: 'string' }] },
                position: { x: 100, y: 200 },
            },
            {
                id: 'ifelse-1',
                type: 'ifelse',
                data: {
                    label: 'Check Length',
                    conditions: [
                        { id: 'c1', variable: 'sys.query', operator: 'contains', value: '?' }
                    ],
                    logicalOperator: 'and',
                },
                position: { x: 350, y: 200 },
            },
            {
                id: 'llm-question',
                type: 'llm',
                data: {
                    label: 'Answer Question',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'Answer questions helpfully.',
                    userPrompt: '{{sys.query}}',
                    temperature: 0.7,
                },
                position: { x: 600, y: 100 },
            },
            {
                id: 'llm-statement',
                type: 'llm',
                data: {
                    label: 'Respond to Statement',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'Acknowledge and expand on statements.',
                    userPrompt: '{{sys.query}}',
                    temperature: 0.7,
                },
                position: { x: 600, y: 300 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: { label: 'Output' },
                position: { x: 850, y: 200 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'ifelse-1' },
            { id: 'e2', source: 'ifelse-1', target: 'llm-question', sourceHandle: 'true' },
            { id: 'e3', source: 'ifelse-1', target: 'llm-statement', sourceHandle: 'false' },
            { id: 'e4', source: 'llm-question', target: 'output-1' },
            { id: 'e5', source: 'llm-statement', target: 'output-1' },
        ],
    },
    data_pipeline: {
        name: 'Data Pipeline',
        description: 'Fetch, process, analyze data',
        icon: Database,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: { label: 'Start', variables: [{ name: 'topic', type: 'string' }] },
                position: { x: 100, y: 200 },
            },
            {
                id: 'code-1',
                type: 'code',
                data: {
                    label: 'Generate Data',
                    language: 'python3',
                    code: `# Simulate fetching data
import json
import random

result = {
    "topic": sys_query if 'sys_query' in dir() else "default",
    "values": [random.randint(1, 100) for _ in range(5)],
    "timestamp": "2024-01-01T00:00:00Z"
}`,
                },
                position: { x: 350, y: 200 },
            },
            {
                id: 'template-1',
                type: 'template',
                data: {
                    label: 'Format Report',
                    template: '## Data Report\n\n**Topic:** {{sys.query}}\n\n**Generated Data:**\n{{code-1.result}}',
                },
                position: { x: 600, y: 200 },
            },
            {
                id: 'llm-1',
                type: 'llm',
                data: {
                    label: 'Analyze',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'Analyze data and provide insights.',
                    userPrompt: 'Analyze this data:\n\n{{template-1.text}}',
                    temperature: 0.7,
                },
                position: { x: 850, y: 200 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: { label: 'Output' },
                position: { x: 1100, y: 200 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'code-1' },
            { id: 'e2', source: 'code-1', target: 'template-1' },
            { id: 'e3', source: 'template-1', target: 'llm-1' },
            { id: 'e4', source: 'llm-1', target: 'output-1' },
        ],
    },

    // ============================================
    // CUSTOMER SUPPORT & ROUTING TEMPLATES
    // ============================================

    support_ticket_router: {
        name: 'Support Ticket Router',
        description: 'Classify and route support tickets with escalation',
        category: 'Customer Support',
        icon: ChatTeardropText,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'ticket_content', type: 'string', required: true },
                        { name: 'customer_id', type: 'string', required: true },
                        { name: 'priority', type: 'string', required: false },
                    ],
                },
                position: { x: 100, y: 250 },
            },
            {
                id: 'classifier-1',
                type: 'classifier',
                data: {
                    label: 'Classify Intent',
                    input: '{{sys.ticket_content}}',
                    categories: [
                        { id: 'billing', name: 'Billing', description: 'Payment, invoices, refunds' },
                        { id: 'technical', name: 'Technical', description: 'Bugs, errors, how-to' },
                        { id: 'account', name: 'Account', description: 'Login, settings, profile' },
                        { id: 'general', name: 'General', description: 'Other inquiries' },
                    ],
                },
                position: { x: 350, y: 250 },
            },
            {
                id: 'ifelse-priority',
                type: 'ifelse',
                data: {
                    label: 'Check Priority',
                    conditions: [
                        { id: 'c1', variable: 'sys.priority', operator: 'equals', value: 'urgent' },
                    ],
                    logicalOperator: 'or',
                },
                position: { x: 600, y: 250 },
            },
            {
                id: 'human-approval',
                type: 'human_in_loop',
                data: {
                    label: 'Manager Review',
                    title: 'Urgent Ticket Escalation',
                    description: 'Review urgent ticket before auto-response',
                    approverType: 'role',
                    approverValue: 'support_manager',
                    options: [
                        { id: 'approve', label: 'Approve Response', action: 'continue' },
                        { id: 'modify', label: 'Modify & Send', action: 'modify' },
                        { id: 'escalate', label: 'Escalate Further', action: 'reject' },
                    ],
                    slaHours: 1,
                },
                position: { x: 850, y: 100 },
            },
            {
                id: 'llm-response',
                type: 'llm',
                data: {
                    label: 'Generate Response',
                    model: 'gpt-4o-mini',
                    systemPrompt: `You are a helpful customer support agent. Generate a professional, empathetic response based on the ticket category and content.

Category: {{classifier-1.category}}
Priority: {{sys.priority}}

Guidelines:
- Be empathetic and professional
- Acknowledge the customer's issue
- Provide clear next steps
- Include relevant help article links when applicable`,
                    userPrompt: 'Customer Ticket:\n{{sys.ticket_content}}\n\nGenerate an appropriate response.',
                    temperature: 0.7,
                    maxTokens: 500,
                },
                position: { x: 850, y: 350 },
            },
            {
                id: 'notification-1',
                type: 'notification',
                data: {
                    label: 'Send Notification',
                    channel: 'email',
                    recipient: '{{sys.customer_id}}',
                    subject: 'Re: Your Support Request',
                    message: '{{llm-response.text}}',
                },
                position: { x: 1100, y: 250 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'ticket_result',
                    outputValue: '{{llm-response.text}}',
                },
                position: { x: 1350, y: 250 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'classifier-1' },
            { id: 'e2', source: 'classifier-1', target: 'ifelse-priority' },
            { id: 'e3', source: 'ifelse-priority', target: 'human-approval', sourceHandle: 'true' },
            { id: 'e4', source: 'ifelse-priority', target: 'llm-response', sourceHandle: 'false' },
            { id: 'e5', source: 'human-approval', target: 'llm-response' },
            { id: 'e6', source: 'llm-response', target: 'notification-1' },
            { id: 'e7', source: 'notification-1', target: 'output-1' },
        ],
    },

    content_moderation: {
        name: 'Content Moderation',
        description: 'Classify content and flag for human review',
        category: 'Customer Support',
        icon: ShieldCheck,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'content', type: 'string', required: true },
                        { name: 'content_type', type: 'string', required: true },
                        { name: 'author_id', type: 'string', required: true },
                    ],
                },
                position: { x: 100, y: 250 },
            },
            {
                id: 'llm-analyze',
                type: 'llm',
                data: {
                    label: 'Analyze Content',
                    model: 'gpt-4o-mini',
                    systemPrompt: `You are a content moderation system. Analyze the content and return a JSON response with:
{
  "safe": boolean,
  "categories": ["spam", "harassment", "hate_speech", "violence", "adult", "none"],
  "confidence": 0-100,
  "reason": "brief explanation"
}`,
                    userPrompt: 'Content Type: {{sys.content_type}}\n\nContent to analyze:\n{{sys.content}}',
                    temperature: 0.1,
                    maxTokens: 300,
                },
                position: { x: 350, y: 250 },
            },
            {
                id: 'param-extract',
                type: 'parameter_extractor',
                data: {
                    label: 'Extract Result',
                    inputText: '{{llm-analyze.text}}',
                    parameters: [
                        { name: 'safe', type: 'boolean', description: 'Whether content is safe' },
                        { name: 'confidence', type: 'number', description: 'Confidence score 0-100' },
                        { name: 'categories', type: 'array', description: 'Violation categories' },
                    ],
                    model: 'gpt-4o-mini',
                },
                position: { x: 600, y: 250 },
            },
            {
                id: 'ifelse-safe',
                type: 'ifelse',
                data: {
                    label: 'Is Safe?',
                    conditions: [
                        { id: 'c1', variable: 'param-extract.safe', operator: 'equals', value: 'true' },
                        { id: 'c2', variable: 'param-extract.confidence', operator: 'greater_than', value: '85' },
                    ],
                    logicalOperator: 'and',
                },
                position: { x: 850, y: 250 },
            },
            {
                id: 'human-review',
                type: 'human_in_loop',
                data: {
                    label: 'Human Review',
                    title: 'Content Flagged for Review',
                    description: 'AI detected potential policy violation',
                    approverType: 'role',
                    approverValue: 'content_moderator',
                    options: [
                        { id: 'approve', label: 'Approve Content', action: 'continue' },
                        { id: 'remove', label: 'Remove Content', action: 'reject' },
                        { id: 'warn', label: 'Warn User', action: 'modify' },
                    ],
                    slaHours: 4,
                },
                position: { x: 1100, y: 100 },
            },
            {
                id: 'template-approved',
                type: 'template',
                data: {
                    label: 'Approved Result',
                    template: `{
  "status": "approved",
  "content_id": "{{sys.content}}",
  "author_id": "{{sys.author_id}}",
  "auto_approved": true
}`,
                },
                position: { x: 1100, y: 350 },
            },
            {
                id: 'aggregator-1',
                type: 'aggregator',
                data: {
                    label: 'Combine Results',
                    inputs: ['human-review', 'template-approved'],
                    aggregationMethod: 'first_available',
                },
                position: { x: 1350, y: 250 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'moderation_result',
                },
                position: { x: 1600, y: 250 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'llm-analyze' },
            { id: 'e2', source: 'llm-analyze', target: 'param-extract' },
            { id: 'e3', source: 'param-extract', target: 'ifelse-safe' },
            { id: 'e4', source: 'ifelse-safe', target: 'human-review', sourceHandle: 'false' },
            { id: 'e5', source: 'ifelse-safe', target: 'template-approved', sourceHandle: 'true' },
            { id: 'e6', source: 'human-review', target: 'aggregator-1' },
            { id: 'e7', source: 'template-approved', target: 'aggregator-1' },
            { id: 'e8', source: 'aggregator-1', target: 'output-1' },
        ],
    },

    // ============================================
    // DOCUMENT PROCESSING TEMPLATES
    // ============================================

    document_extraction: {
        name: 'Document Extraction',
        description: 'Extract and structure information from documents',
        category: 'Document Processing',
        icon: MagnifyingGlass,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'document_url', type: 'string', required: true },
                        { name: 'extraction_type', type: 'string', required: true },
                    ],
                },
                position: { x: 100, y: 250 },
            },
            {
                id: 'doc-extract',
                type: 'document_extractor',
                data: {
                    label: 'Extract Text',
                    document: '{{sys.document_url}}',
                    extractionMethod: 'auto',
                    outputFormat: 'markdown',
                },
                position: { x: 350, y: 250 },
            },
            {
                id: 'llm-summarize',
                type: 'llm',
                data: {
                    label: 'Summarize Document',
                    model: 'gpt-4o-mini',
                    systemPrompt: `Analyze the document and provide:
1. A concise summary (2-3 sentences)
2. Key points (bullet list)
3. Document type classification
4. Any important dates or numbers mentioned`,
                    userPrompt: 'Document content:\n\n{{doc-extract.text}}',
                    temperature: 0.3,
                    maxTokens: 1000,
                },
                position: { x: 600, y: 150 },
            },
            {
                id: 'param-extract',
                type: 'parameter_extractor',
                data: {
                    label: 'Extract Fields',
                    inputText: '{{doc-extract.text}}',
                    parameters: [
                        { name: 'dates', type: 'array', description: 'All dates mentioned' },
                        { name: 'amounts', type: 'array', description: 'Monetary amounts' },
                        { name: 'names', type: 'array', description: 'Person or company names' },
                        { name: 'addresses', type: 'array', description: 'Physical addresses' },
                    ],
                    model: 'gpt-4o-mini',
                },
                position: { x: 600, y: 350 },
            },
            {
                id: 'aggregator-1',
                type: 'aggregator',
                data: {
                    label: 'Combine Results',
                    inputs: ['llm-summarize', 'param-extract'],
                    aggregationMethod: 'merge',
                },
                position: { x: 900, y: 250 },
            },
            {
                id: 'template-1',
                type: 'template',
                data: {
                    label: 'Format Output',
                    template: `# Document Analysis Report

## Summary
{{llm-summarize.text}}

## Extracted Data
- **Dates Found:** {{param-extract.dates}}
- **Amounts Found:** {{param-extract.amounts}}
- **Names Found:** {{param-extract.names}}
- **Addresses Found:** {{param-extract.addresses}}

---
*Extraction Type: {{sys.extraction_type}}*`,
                },
                position: { x: 1150, y: 250 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'analysis_report',
                    outputValue: '{{template-1.text}}',
                },
                position: { x: 1400, y: 250 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'doc-extract' },
            { id: 'e2', source: 'doc-extract', target: 'llm-summarize' },
            { id: 'e3', source: 'doc-extract', target: 'param-extract' },
            { id: 'e4', source: 'llm-summarize', target: 'aggregator-1' },
            { id: 'e5', source: 'param-extract', target: 'aggregator-1' },
            { id: 'e6', source: 'aggregator-1', target: 'template-1' },
            { id: 'e7', source: 'template-1', target: 'output-1' },
        ],
    },

    batch_document_analysis: {
        name: 'Batch Document Analysis',
        description: 'Process multiple documents with iteration',
        category: 'Document Processing',
        icon: FileText,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'document_list', type: 'array', required: true },
                        { name: 'analysis_prompt', type: 'string', required: true },
                    ],
                },
                position: { x: 100, y: 250 },
            },
            {
                id: 'iteration-1',
                type: 'iteration',
                data: {
                    label: 'For Each Document',
                    inputList: '{{sys.document_list}}',
                    loopVariable: 'current_doc',
                    outputVariable: 'analysis_results',
                },
                position: { x: 350, y: 250 },
            },
            {
                id: 'llm-analyze',
                type: 'llm',
                data: {
                    label: 'Analyze Document',
                    model: 'gpt-4o-mini',
                    systemPrompt: '{{sys.analysis_prompt}}',
                    userPrompt: 'Analyze this document:\n\n{{iteration-1.current_doc}}',
                    temperature: 0.5,
                    maxTokens: 800,
                },
                position: { x: 600, y: 250 },
            },
            {
                id: 'code-1',
                type: 'code',
                data: {
                    label: 'Format Result',
                    language: 'python3',
                    code: `# Format the analysis result
result = {
    "document": current_doc[:100] + "..." if len(current_doc) > 100 else current_doc,
    "analysis": llm_analyze_text,
    "processed": True
}`,
                },
                position: { x: 850, y: 250 },
            },
            {
                id: 'list-op-1',
                type: 'list_operator',
                data: {
                    label: 'Filter Successful',
                    operation: 'filter',
                    inputList: '{{iteration-1.analysis_results}}',
                    criteria: 'item.processed == true',
                },
                position: { x: 1100, y: 250 },
            },
            {
                id: 'llm-summary',
                type: 'llm',
                data: {
                    label: 'Generate Summary',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'Synthesize the individual document analyses into a cohesive executive summary highlighting key themes and insights.',
                    userPrompt: 'Individual analyses:\n\n{{list-op-1.result}}',
                    temperature: 0.7,
                    maxTokens: 1000,
                },
                position: { x: 1350, y: 250 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'batch_analysis',
                },
                position: { x: 1600, y: 250 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'iteration-1' },
            { id: 'e2', source: 'iteration-1', target: 'llm-analyze', sourceHandle: 'loop' },
            { id: 'e3', source: 'llm-analyze', target: 'code-1' },
            { id: 'e4', source: 'code-1', target: 'iteration-1', targetHandle: 'loop-end' },
            { id: 'e5', source: 'iteration-1', target: 'list-op-1', sourceHandle: 'done' },
            { id: 'e6', source: 'list-op-1', target: 'llm-summary' },
            { id: 'e7', source: 'llm-summary', target: 'output-1' },
        ],
    },

    // ============================================
    // RESEARCH & ANALYSIS TEMPLATES
    // ============================================

    multi_source_research: {
        name: 'Multi-Source Research',
        description: 'Research topic from multiple knowledge bases',
        category: 'Research & Analysis',
        icon: BookOpen,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'research_question', type: 'string', required: true },
                        { name: 'sources', type: 'array', required: false },
                    ],
                },
                position: { x: 100, y: 300 },
            },
            {
                id: 'knowledge-1',
                type: 'knowledge',
                data: {
                    label: 'Search Internal Docs',
                    query: '{{sys.research_question}}',
                    knowledgeBase: 'internal_documents',
                    numResults: 5,
                },
                position: { x: 400, y: 150 },
            },
            {
                id: 'knowledge-2',
                type: 'knowledge',
                data: {
                    label: 'Search Industry Data',
                    query: '{{sys.research_question}}',
                    knowledgeBase: 'industry_reports',
                    numResults: 5,
                },
                position: { x: 400, y: 300 },
            },
            {
                id: 'http-1',
                type: 'http',
                data: {
                    label: 'Fetch External Data',
                    url: 'https://api.example.com/search',
                    method: 'GET',
                    headers: { 'Content-Type': 'application/json' },
                    queryParams: { q: '{{sys.research_question}}' },
                },
                position: { x: 400, y: 450 },
            },
            {
                id: 'aggregator-1',
                type: 'aggregator',
                data: {
                    label: 'Combine Sources',
                    inputs: ['knowledge-1', 'knowledge-2', 'http-1'],
                    aggregationMethod: 'merge',
                },
                position: { x: 700, y: 300 },
            },
            {
                id: 'llm-analyze',
                type: 'llm',
                data: {
                    label: 'Synthesize Research',
                    model: 'gpt-4o',
                    systemPrompt: `You are a research analyst. Synthesize information from multiple sources into a comprehensive research report.

Structure your response:
1. Executive Summary
2. Key Findings
3. Supporting Evidence (cite sources)
4. Gaps in Information
5. Recommendations`,
                    userPrompt: `Research Question: {{sys.research_question}}

Sources:
{{aggregator-1.result}}`,
                    temperature: 0.5,
                    maxTokens: 2000,
                },
                position: { x: 1000, y: 300 },
            },
            {
                id: 'template-1',
                type: 'template',
                data: {
                    label: 'Format Report',
                    template: `# Research Report

**Question:** {{sys.research_question}}
**Generated:** {{sys.timestamp}}

{{llm-analyze.text}}

---
*Sources consulted: Internal Documents, Industry Reports, External APIs*`,
                },
                position: { x: 1300, y: 300 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'research_report',
                    outputValue: '{{template-1.text}}',
                },
                position: { x: 1550, y: 300 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'knowledge-1' },
            { id: 'e2', source: 'start-1', target: 'knowledge-2' },
            { id: 'e3', source: 'start-1', target: 'http-1' },
            { id: 'e4', source: 'knowledge-1', target: 'aggregator-1' },
            { id: 'e5', source: 'knowledge-2', target: 'aggregator-1' },
            { id: 'e6', source: 'http-1', target: 'aggregator-1' },
            { id: 'e7', source: 'aggregator-1', target: 'llm-analyze' },
            { id: 'e8', source: 'llm-analyze', target: 'template-1' },
            { id: 'e9', source: 'template-1', target: 'output-1' },
        ],
    },

    competitive_analysis: {
        name: 'Competitive Analysis',
        description: 'Multi-agent competitive intelligence workflow',
        category: 'Research & Analysis',
        icon: Scales,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'company_name', type: 'string', required: true },
                        { name: 'competitors', type: 'array', required: true },
                        { name: 'focus_areas', type: 'array', required: false },
                    ],
                },
                position: { x: 100, y: 300 },
            },
            {
                id: 'agent-market',
                type: 'agent',
                data: {
                    label: 'Market Research Agent',
                    agentId: 'research',
                    agentName: 'Market Researcher',
                    agentType: 'research',
                    userPrompt: `Research market position and trends for {{sys.company_name}} compared to {{sys.competitors}}.

Focus on:
- Market share estimates
- Growth trajectories
- Key differentiators`,
                    model: 'gpt-4o',
                },
                position: { x: 400, y: 150 },
            },
            {
                id: 'agent-product',
                type: 'agent',
                data: {
                    label: 'Product Analysis Agent',
                    agentId: 'analysis',
                    agentName: 'Product Analyst',
                    agentType: 'analysis',
                    userPrompt: `Compare product offerings between {{sys.company_name}} and {{sys.competitors}}.

Analyze:
- Feature comparison
- Pricing strategies
- Technology stack (if known)`,
                    model: 'gpt-4o',
                },
                position: { x: 400, y: 300 },
            },
            {
                id: 'agent-sentiment',
                type: 'agent',
                data: {
                    label: 'Sentiment Analysis Agent',
                    agentId: 'analysis',
                    agentName: 'Sentiment Analyzer',
                    agentType: 'analysis',
                    userPrompt: `Analyze public sentiment for {{sys.company_name}} vs {{sys.competitors}}.

Consider:
- Customer reviews
- Social media presence
- News sentiment`,
                    model: 'gpt-4o',
                },
                position: { x: 400, y: 450 },
            },
            {
                id: 'aggregator-1',
                type: 'aggregator',
                data: {
                    label: 'Combine Analyses',
                    inputs: ['agent-market', 'agent-product', 'agent-sentiment'],
                    aggregationMethod: 'merge',
                },
                position: { x: 750, y: 300 },
            },
            {
                id: 'llm-synthesis',
                type: 'llm',
                data: {
                    label: 'Synthesize Insights',
                    model: 'gpt-4o',
                    systemPrompt: `You are a strategic analyst. Synthesize the multi-dimensional competitive analysis into actionable insights.

Output structure:
1. Executive Summary
2. SWOT Analysis
3. Competitive Positioning Map (describe)
4. Strategic Recommendations
5. Key Risks & Opportunities`,
                    userPrompt: 'Combined analysis:\n\n{{aggregator-1.result}}',
                    temperature: 0.6,
                    maxTokens: 2500,
                },
                position: { x: 1050, y: 300 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'competitive_report',
                },
                position: { x: 1350, y: 300 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'agent-market' },
            { id: 'e2', source: 'start-1', target: 'agent-product' },
            { id: 'e3', source: 'start-1', target: 'agent-sentiment' },
            { id: 'e4', source: 'agent-market', target: 'aggregator-1' },
            { id: 'e5', source: 'agent-product', target: 'aggregator-1' },
            { id: 'e6', source: 'agent-sentiment', target: 'aggregator-1' },
            { id: 'e7', source: 'aggregator-1', target: 'llm-synthesis' },
            { id: 'e8', source: 'llm-synthesis', target: 'output-1' },
        ],
    },

    // ============================================
    // DATA PROCESSING TEMPLATES
    // ============================================

    api_data_enrichment: {
        name: 'API Data Enrichment',
        description: 'Fetch, validate, and enrich external data',
        category: 'Data Processing',
        icon: Globe,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'api_endpoint', type: 'string', required: true },
                        { name: 'api_key', type: 'string', required: false },
                        { name: 'enrichment_fields', type: 'array', required: false },
                    ],
                },
                position: { x: 100, y: 250 },
            },
            {
                id: 'http-1',
                type: 'http',
                data: {
                    label: 'Fetch API Data',
                    url: '{{sys.api_endpoint}}',
                    method: 'GET',
                    headers: {
                        'Authorization': 'Bearer {{sys.api_key}}',
                        'Content-Type': 'application/json',
                    },
                },
                position: { x: 350, y: 250 },
            },
            {
                id: 'code-validate',
                type: 'code',
                data: {
                    label: 'Validate Response',
                    language: 'python3',
                    code: `import json

# Parse and validate API response
try:
    data = json.loads(http_1_result) if isinstance(http_1_result, str) else http_1_result
    valid = True
    errors = []

    # Check required fields
    required_fields = ['id', 'name', 'status']
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing field: {field}")
            valid = False

    result = {
        "valid": valid,
        "data": data if valid else None,
        "errors": errors
    }
except Exception as e:
    result = {"valid": False, "data": None, "errors": [str(e)]}`,
                },
                position: { x: 600, y: 250 },
            },
            {
                id: 'ifelse-valid',
                type: 'ifelse',
                data: {
                    label: 'Is Valid?',
                    conditions: [
                        { id: 'c1', variable: 'code-validate.valid', operator: 'equals', value: 'true' },
                    ],
                    logicalOperator: 'and',
                },
                position: { x: 850, y: 250 },
            },
            {
                id: 'llm-enrich',
                type: 'llm',
                data: {
                    label: 'Enrich Data',
                    model: 'gpt-4o-mini',
                    systemPrompt: `You are a data enrichment system. Given raw data, add meaningful derived fields.

Add these enrichments:
- Categorization
- Sentiment (if applicable)
- Risk score (if applicable)
- Summary description

Return as valid JSON.`,
                    userPrompt: 'Raw data to enrich:\n{{code-validate.data}}',
                    temperature: 0.3,
                    maxTokens: 1000,
                },
                position: { x: 1100, y: 150 },
            },
            {
                id: 'template-error',
                type: 'template',
                data: {
                    label: 'Error Response',
                    template: `{
  "status": "error",
  "message": "Data validation failed",
  "errors": {{code-validate.errors}}
}`,
                },
                position: { x: 1100, y: 350 },
            },
            {
                id: 'aggregator-1',
                type: 'aggregator',
                data: {
                    label: 'Combine Results',
                    inputs: ['llm-enrich', 'template-error'],
                    aggregationMethod: 'first_available',
                },
                position: { x: 1350, y: 250 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'enriched_data',
                },
                position: { x: 1600, y: 250 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'http-1' },
            { id: 'e2', source: 'http-1', target: 'code-validate' },
            { id: 'e3', source: 'code-validate', target: 'ifelse-valid' },
            { id: 'e4', source: 'ifelse-valid', target: 'llm-enrich', sourceHandle: 'true' },
            { id: 'e5', source: 'ifelse-valid', target: 'template-error', sourceHandle: 'false' },
            { id: 'e6', source: 'llm-enrich', target: 'aggregator-1' },
            { id: 'e7', source: 'template-error', target: 'aggregator-1' },
            { id: 'e8', source: 'aggregator-1', target: 'output-1' },
        ],
    },

    data_transformation_pipeline: {
        name: 'Data Transformation Pipeline',
        description: 'Transform and process data with multiple operations',
        category: 'Data Processing',
        icon: ArrowsClockwise,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'input_data', type: 'array', required: true },
                        { name: 'filter_criteria', type: 'string', required: false },
                        { name: 'sort_by', type: 'string', required: false },
                    ],
                },
                position: { x: 100, y: 250 },
            },
            {
                id: 'list-filter',
                type: 'list_operator',
                data: {
                    label: 'Filter Data',
                    operation: 'filter',
                    inputList: '{{sys.input_data}}',
                    criteria: '{{sys.filter_criteria}}',
                },
                position: { x: 350, y: 250 },
            },
            {
                id: 'list-sort',
                type: 'list_operator',
                data: {
                    label: 'Sort Data',
                    operation: 'sort',
                    inputList: '{{list-filter.result}}',
                    criteria: '{{sys.sort_by}}',
                    sortOrder: 'desc',
                },
                position: { x: 600, y: 250 },
            },
            {
                id: 'iteration-1',
                type: 'iteration',
                data: {
                    label: 'Transform Each',
                    inputList: '{{list-sort.result}}',
                    loopVariable: 'item',
                    outputVariable: 'transformed_items',
                },
                position: { x: 850, y: 250 },
            },
            {
                id: 'code-transform',
                type: 'code',
                data: {
                    label: 'Apply Transform',
                    language: 'python3',
                    code: `# Transform individual item
transformed = {
    **item,
    "processed": True,
    "normalized_value": item.get("value", 0) / 100,
    "category": "high" if item.get("value", 0) > 50 else "low"
}
result = transformed`,
                },
                position: { x: 1100, y: 250 },
            },
            {
                id: 'var-assign',
                type: 'variable_assigner',
                data: {
                    label: 'Store Result',
                    variableName: 'final_dataset',
                    value: '{{iteration-1.transformed_items}}',
                },
                position: { x: 1350, y: 250 },
            },
            {
                id: 'llm-summary',
                type: 'llm',
                data: {
                    label: 'Generate Summary',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'Analyze the transformed dataset and provide statistics and insights.',
                    userPrompt: 'Transformed data:\n{{var-assign.final_dataset}}',
                    temperature: 0.5,
                    maxTokens: 500,
                },
                position: { x: 1600, y: 250 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'transformation_result',
                },
                position: { x: 1850, y: 250 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'list-filter' },
            { id: 'e2', source: 'list-filter', target: 'list-sort' },
            { id: 'e3', source: 'list-sort', target: 'iteration-1' },
            { id: 'e4', source: 'iteration-1', target: 'code-transform', sourceHandle: 'loop' },
            { id: 'e5', source: 'code-transform', target: 'iteration-1', targetHandle: 'loop-end' },
            { id: 'e6', source: 'iteration-1', target: 'var-assign', sourceHandle: 'done' },
            { id: 'e7', source: 'var-assign', target: 'llm-summary' },
            { id: 'e8', source: 'llm-summary', target: 'output-1' },
        ],
    },

    // ============================================
    // COMPLIANCE & APPROVAL TEMPLATES
    // ============================================

    compliance_review: {
        name: 'Compliance Review',
        description: 'Automated compliance check with escalation',
        category: 'Compliance',
        icon: ShieldCheck,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'document', type: 'string', required: true },
                        { name: 'compliance_type', type: 'string', required: true },
                        { name: 'jurisdiction', type: 'string', required: false },
                    ],
                },
                position: { x: 100, y: 300 },
            },
            {
                id: 'agent-compliance',
                type: 'agent',
                data: {
                    label: 'Compliance Agent',
                    agentId: 'compliance',
                    agentName: 'Compliance Checker',
                    agentType: 'compliance',
                    userPrompt: `Review the following document for {{sys.compliance_type}} compliance.

Jurisdiction: {{sys.jurisdiction}}

Document:
{{sys.document}}

Provide:
1. Compliance status (compliant/non-compliant/needs-review)
2. List of issues found
3. Risk level (low/medium/high/critical)
4. Recommended actions`,
                    model: 'gpt-4o',
                },
                position: { x: 400, y: 300 },
            },
            {
                id: 'param-extract',
                type: 'parameter_extractor',
                data: {
                    label: 'Extract Findings',
                    inputText: '{{agent-compliance.text}}',
                    parameters: [
                        { name: 'status', type: 'string', description: 'Compliance status' },
                        { name: 'risk_level', type: 'string', description: 'Risk level' },
                        { name: 'issues', type: 'array', description: 'Issues found' },
                    ],
                    model: 'gpt-4o-mini',
                },
                position: { x: 700, y: 300 },
            },
            {
                id: 'ifelse-risk',
                type: 'ifelse',
                data: {
                    label: 'High Risk?',
                    conditions: [
                        { id: 'c1', variable: 'param-extract.risk_level', operator: 'equals', value: 'critical' },
                        { id: 'c2', variable: 'param-extract.risk_level', operator: 'equals', value: 'high' },
                    ],
                    logicalOperator: 'or',
                },
                position: { x: 1000, y: 300 },
            },
            {
                id: 'human-approval',
                type: 'human_in_loop',
                data: {
                    label: 'Legal Review',
                    title: 'High-Risk Compliance Finding',
                    description: 'Document flagged for legal/compliance team review',
                    approverType: 'role',
                    approverValue: 'compliance_officer',
                    options: [
                        { id: 'approve', label: 'Accept Finding', action: 'continue' },
                        { id: 'override', label: 'Override (with justification)', action: 'modify' },
                        { id: 'escalate', label: 'Escalate to Legal', action: 'escalate' },
                    ],
                    slaHours: 24,
                },
                position: { x: 1300, y: 150 },
            },
            {
                id: 'notification-1',
                type: 'notification',
                data: {
                    label: 'Alert Team',
                    channel: 'slack',
                    recipient: '#compliance-alerts',
                    message: 'High-risk compliance finding requires review: {{param-extract.status}}',
                },
                position: { x: 1300, y: 300 },
            },
            {
                id: 'template-report',
                type: 'template',
                data: {
                    label: 'Generate Report',
                    template: `# Compliance Review Report

## Summary
- **Status:** {{param-extract.status}}
- **Risk Level:** {{param-extract.risk_level}}
- **Compliance Type:** {{sys.compliance_type}}
- **Jurisdiction:** {{sys.jurisdiction}}

## Issues Found
{{param-extract.issues}}

## Full Analysis
{{agent-compliance.text}}

---
*Report generated automatically. High-risk findings require human approval.*`,
                },
                position: { x: 1300, y: 450 },
            },
            {
                id: 'aggregator-1',
                type: 'aggregator',
                data: {
                    label: 'Combine Outputs',
                    inputs: ['human-approval', 'template-report'],
                    aggregationMethod: 'merge',
                },
                position: { x: 1600, y: 300 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'compliance_report',
                },
                position: { x: 1850, y: 300 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'agent-compliance' },
            { id: 'e2', source: 'agent-compliance', target: 'param-extract' },
            { id: 'e3', source: 'param-extract', target: 'ifelse-risk' },
            { id: 'e4', source: 'ifelse-risk', target: 'human-approval', sourceHandle: 'true' },
            { id: 'e5', source: 'ifelse-risk', target: 'notification-1', sourceHandle: 'true' },
            { id: 'e6', source: 'ifelse-risk', target: 'template-report', sourceHandle: 'false' },
            { id: 'e7', source: 'human-approval', target: 'aggregator-1' },
            { id: 'e8', source: 'template-report', target: 'aggregator-1' },
            { id: 'e9', source: 'aggregator-1', target: 'output-1' },
        ],
    },

    // ============================================
    // ORCHESTRATION TEMPLATES
    // ============================================

    subworkflow_orchestration: {
        name: 'Sub-Workflow Orchestration',
        description: 'Orchestrate multiple sub-workflows with routing',
        category: 'Orchestration',
        icon: TreeStructure,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'request_type', type: 'string', required: true },
                        { name: 'payload', type: 'object', required: true },
                    ],
                },
                position: { x: 100, y: 300 },
            },
            {
                id: 'classifier-1',
                type: 'classifier',
                data: {
                    label: 'Route Request',
                    input: '{{sys.request_type}}',
                    categories: [
                        { id: 'analysis', name: 'Analysis', description: 'Data analysis tasks' },
                        { id: 'generation', name: 'Generation', description: 'Content generation' },
                        { id: 'processing', name: 'Processing', description: 'Data processing' },
                    ],
                },
                position: { x: 400, y: 300 },
            },
            {
                id: 'subworkflow-analysis',
                type: 'subworkflow',
                data: {
                    label: 'Analysis Workflow',
                    workflowId: 'analysis-workflow-id',
                    inputMappings: [
                        { sourceVariable: 'sys.payload', targetVariable: 'input_data' },
                    ],
                    outputVariable: 'analysis_result',
                    async: false,
                    timeout: 300000,
                },
                position: { x: 750, y: 150 },
            },
            {
                id: 'subworkflow-generation',
                type: 'subworkflow',
                data: {
                    label: 'Generation Workflow',
                    workflowId: 'generation-workflow-id',
                    inputMappings: [
                        { sourceVariable: 'sys.payload', targetVariable: 'input_prompt' },
                    ],
                    outputVariable: 'generation_result',
                    async: false,
                    timeout: 300000,
                },
                position: { x: 750, y: 300 },
            },
            {
                id: 'subworkflow-processing',
                type: 'subworkflow',
                data: {
                    label: 'Processing Workflow',
                    workflowId: 'processing-workflow-id',
                    inputMappings: [
                        { sourceVariable: 'sys.payload', targetVariable: 'raw_data' },
                    ],
                    outputVariable: 'processing_result',
                    async: false,
                    timeout: 300000,
                },
                position: { x: 750, y: 450 },
            },
            {
                id: 'aggregator-1',
                type: 'aggregator',
                data: {
                    label: 'Collect Results',
                    inputs: ['subworkflow-analysis', 'subworkflow-generation', 'subworkflow-processing'],
                    aggregationMethod: 'first_available',
                },
                position: { x: 1100, y: 300 },
            },
            {
                id: 'llm-postprocess',
                type: 'llm',
                data: {
                    label: 'Post-Process',
                    model: 'gpt-4o-mini',
                    systemPrompt: 'Format the workflow result for the end user. Ensure clarity and completeness.',
                    userPrompt: 'Result to format:\n{{aggregator-1.result}}',
                    temperature: 0.5,
                    maxTokens: 500,
                },
                position: { x: 1350, y: 300 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'orchestration_result',
                },
                position: { x: 1600, y: 300 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'classifier-1' },
            { id: 'e2', source: 'classifier-1', target: 'subworkflow-analysis', sourceHandle: 'analysis' },
            { id: 'e3', source: 'classifier-1', target: 'subworkflow-generation', sourceHandle: 'generation' },
            { id: 'e4', source: 'classifier-1', target: 'subworkflow-processing', sourceHandle: 'processing' },
            { id: 'e5', source: 'subworkflow-analysis', target: 'aggregator-1' },
            { id: 'e6', source: 'subworkflow-generation', target: 'aggregator-1' },
            { id: 'e7', source: 'subworkflow-processing', target: 'aggregator-1' },
            { id: 'e8', source: 'aggregator-1', target: 'llm-postprocess' },
            { id: 'e9', source: 'llm-postprocess', target: 'output-1' },
        ],
    },

    multi_agent_collaboration: {
        name: 'Multi-Agent Collaboration',
        description: 'Multiple specialized agents working together',
        category: 'Orchestration',
        icon: Users,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'task', type: 'string', required: true },
                        { name: 'context', type: 'string', required: false },
                    ],
                },
                position: { x: 100, y: 300 },
            },
            {
                id: 'llm-planner',
                type: 'llm',
                data: {
                    label: 'Task Planner',
                    model: 'gpt-4o',
                    systemPrompt: `You are a task planner. Break down the task into subtasks that can be handled by specialized agents:
- Research Agent: Information gathering and analysis
- Writer Agent: Content creation and documentation
- Reviewer Agent: Quality assurance and feedback

Output a JSON plan with subtasks assigned to each agent.`,
                    userPrompt: 'Task: {{sys.task}}\n\nContext: {{sys.context}}',
                    temperature: 0.5,
                    maxTokens: 1000,
                },
                position: { x: 400, y: 300 },
            },
            {
                id: 'agent-research',
                type: 'agent',
                data: {
                    label: 'Research Agent',
                    agentId: 'research',
                    agentName: 'Researcher',
                    agentType: 'research',
                    userPrompt: 'Based on the plan, perform research:\n\nTask: {{sys.task}}\nPlan: {{llm-planner.text}}',
                    model: 'gpt-4o',
                },
                position: { x: 750, y: 150 },
            },
            {
                id: 'agent-writer',
                type: 'agent',
                data: {
                    label: 'Writer Agent',
                    agentId: 'analysis',
                    agentName: 'Writer',
                    agentType: 'analysis',
                    userPrompt: 'Using the research findings, create content:\n\nResearch: {{agent-research.text}}\nTask: {{sys.task}}',
                    model: 'gpt-4o',
                },
                position: { x: 1100, y: 150 },
            },
            {
                id: 'agent-reviewer',
                type: 'agent',
                data: {
                    label: 'Reviewer Agent',
                    agentId: 'compliance',
                    agentName: 'Reviewer',
                    agentType: 'compliance',
                    userPrompt: 'Review the content for quality and accuracy:\n\nContent: {{agent-writer.text}}\nOriginal Task: {{sys.task}}',
                    model: 'gpt-4o',
                },
                position: { x: 1100, y: 350 },
            },
            {
                id: 'aggregator-1',
                type: 'aggregator',
                data: {
                    label: 'Combine Work',
                    inputs: ['agent-writer', 'agent-reviewer'],
                    aggregationMethod: 'merge',
                },
                position: { x: 1400, y: 250 },
            },
            {
                id: 'llm-final',
                type: 'llm',
                data: {
                    label: 'Final Assembly',
                    model: 'gpt-4o',
                    systemPrompt: 'Combine the writer output with reviewer feedback to produce the final deliverable.',
                    userPrompt: 'Content and Review:\n{{aggregator-1.result}}',
                    temperature: 0.5,
                    maxTokens: 2000,
                },
                position: { x: 1650, y: 250 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'collaboration_result',
                },
                position: { x: 1900, y: 250 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'llm-planner' },
            { id: 'e2', source: 'llm-planner', target: 'agent-research' },
            { id: 'e3', source: 'agent-research', target: 'agent-writer' },
            { id: 'e4', source: 'agent-writer', target: 'agent-reviewer' },
            { id: 'e5', source: 'agent-writer', target: 'aggregator-1' },
            { id: 'e6', source: 'agent-reviewer', target: 'aggregator-1' },
            { id: 'e7', source: 'aggregator-1', target: 'llm-final' },
            { id: 'e8', source: 'llm-final', target: 'output-1' },
        ],
    },

    // ============================================
    // SCHEDULED & AUTOMATED TEMPLATES
    // ============================================

    scheduled_report_generator: {
        name: 'Scheduled Report Generator',
        description: 'Generate and distribute reports on schedule',
        category: 'Automation',
        icon: Robot,
        nodes: [
            {
                id: 'start-1',
                type: 'start',
                data: {
                    label: 'Start',
                    variables: [
                        { name: 'report_type', type: 'string', required: true },
                        { name: 'date_range', type: 'string', required: true },
                        { name: 'recipients', type: 'array', required: true },
                    ],
                },
                position: { x: 100, y: 250 },
            },
            {
                id: 'http-data',
                type: 'http',
                data: {
                    label: 'Fetch Metrics',
                    url: 'https://api.example.com/metrics',
                    method: 'GET',
                    headers: { 'Content-Type': 'application/json' },
                    queryParams: {
                        type: '{{sys.report_type}}',
                        range: '{{sys.date_range}}',
                    },
                },
                position: { x: 400, y: 250 },
            },
            {
                id: 'code-process',
                type: 'code',
                data: {
                    label: 'Process Metrics',
                    language: 'python3',
                    code: `import json

# Process raw metrics
data = json.loads(http_data_result) if isinstance(http_data_result, str) else http_data_result

# Calculate summary statistics
summary = {
    "total": sum(d.get("value", 0) for d in data.get("metrics", [])),
    "average": sum(d.get("value", 0) for d in data.get("metrics", [])) / max(len(data.get("metrics", [])), 1),
    "count": len(data.get("metrics", [])),
    "period": sys_date_range
}

result = summary`,
                },
                position: { x: 650, y: 250 },
            },
            {
                id: 'llm-analysis',
                type: 'llm',
                data: {
                    label: 'Generate Analysis',
                    model: 'gpt-4o-mini',
                    systemPrompt: `Generate a professional business report analysis based on the metrics.

Include:
1. Executive Summary
2. Key Metrics Overview
3. Trends & Insights
4. Recommendations`,
                    userPrompt: 'Metrics Summary:\n{{code-process.result}}\n\nReport Type: {{sys.report_type}}',
                    temperature: 0.6,
                    maxTokens: 1500,
                },
                position: { x: 900, y: 250 },
            },
            {
                id: 'template-report',
                type: 'template',
                data: {
                    label: 'Format Report',
                    template: `# {{sys.report_type}} Report

**Period:** {{sys.date_range}}
**Generated:** {{sys.timestamp}}

{{llm-analysis.text}}

---
*This report was automatically generated.*`,
                },
                position: { x: 1150, y: 250 },
            },
            {
                id: 'iteration-notify',
                type: 'iteration',
                data: {
                    label: 'For Each Recipient',
                    inputList: '{{sys.recipients}}',
                    loopVariable: 'recipient',
                    outputVariable: 'notifications_sent',
                },
                position: { x: 1400, y: 250 },
            },
            {
                id: 'notification-1',
                type: 'notification',
                data: {
                    label: 'Send Report',
                    channel: 'email',
                    recipient: '{{iteration-notify.recipient}}',
                    subject: '{{sys.report_type}} Report - {{sys.date_range}}',
                    message: '{{template-report.text}}',
                },
                position: { x: 1650, y: 250 },
            },
            {
                id: 'output-1',
                type: 'output',
                data: {
                    label: 'Output',
                    outputName: 'report_result',
                },
                position: { x: 1900, y: 250 },
            },
        ],
        edges: [
            { id: 'e1', source: 'start-1', target: 'http-data' },
            { id: 'e2', source: 'http-data', target: 'code-process' },
            { id: 'e3', source: 'code-process', target: 'llm-analysis' },
            { id: 'e4', source: 'llm-analysis', target: 'template-report' },
            { id: 'e5', source: 'template-report', target: 'iteration-notify' },
            { id: 'e6', source: 'iteration-notify', target: 'notification-1', sourceHandle: 'loop' },
            { id: 'e7', source: 'notification-1', target: 'iteration-notify', targetHandle: 'loop-end' },
            { id: 'e8', source: 'iteration-notify', target: 'output-1', sourceHandle: 'done' },
        ],
    },
};

// Template categories for organized display
const TEMPLATE_CATEGORIES = {
    'Getting Started': ['simple_qa', 'code_generation', 'conditional', 'data_pipeline'],
    'Customer Support': ['support_ticket_router', 'content_moderation'],
    'Document Processing': ['document_extraction', 'batch_document_analysis'],
    'Research & Analysis': ['multi_source_research', 'competitive_analysis'],
    'Data Processing': ['api_data_enrichment', 'data_transformation_pipeline'],
    'Compliance': ['compliance_review'],
    'Orchestration': ['subworkflow_orchestration', 'multi_agent_collaboration'],
    'Automation': ['scheduled_report_generator'],
};

interface ExampleWorkflowLoaderProps {
    onLoad: (nodes: Node[], edges: Edge[]) => void;
}

// Category icons mapping
const CATEGORY_ICONS: Record<string, Icon> = {
    'Getting Started': Lightning,
    'Customer Support': ChatTeardropText,
    'Document Processing': MagnifyingGlass,
    'Research & Analysis': FunnelSimple,
    'Data Processing': Database,
    'Compliance': ShieldCheck,
    'Orchestration': TreeStructure,
    'Automation': Robot,
};

export function ExampleWorkflowLoader({ onLoad }: ExampleWorkflowLoaderProps) {
    const handleLoadExample = (key: keyof typeof EXAMPLE_WORKFLOWS) => {
        const workflow = EXAMPLE_WORKFLOWS[key];
        onLoad(workflow.nodes as Node[], workflow.edges as Edge[]);
    };

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2">
                    <Lightning className="h-4 w-4" weight="fill" />
                    Templates
                    <CaretDown className="h-3 w-3" />
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-72">
                <DropdownMenuLabel>Workflow Templates</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {Object.entries(TEMPLATE_CATEGORIES).map(([category, workflowKeys]) => {
                    const CategoryIcon = CATEGORY_ICONS[category] || Lightning;
                    return (
                        <DropdownMenuSub key={category}>
                            <DropdownMenuSubTrigger className="flex items-center gap-2">
                                <CategoryIcon className="h-4 w-4 text-muted-foreground" weight="bold" />
                                <span>{category}</span>
                                <span className="ml-auto text-xs text-muted-foreground">
                                    {workflowKeys.length}
                                </span>
                            </DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="w-72">
                                {workflowKeys.map((key) => {
                                    const workflow = EXAMPLE_WORKFLOWS[key as keyof typeof EXAMPLE_WORKFLOWS];
                                    if (!workflow) return null;
                                    const Icon = workflow.icon;
                                    return (
                                        <DropdownMenuItem
                                            key={key}
                                            onClick={() => handleLoadExample(key as keyof typeof EXAMPLE_WORKFLOWS)}
                                            className="flex items-start gap-3 py-2"
                                        >
                                            <Icon className="h-4 w-4 mt-0.5 text-muted-foreground" weight="bold" />
                                            <div className="flex-1">
                                                <div className="font-medium">{workflow.name}</div>
                                                <div className="text-xs text-muted-foreground line-clamp-2">
                                                    {workflow.description}
                                                </div>
                                            </div>
                                        </DropdownMenuItem>
                                    );
                                })}
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                    );
                })}
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="text-xs text-muted-foreground font-normal">
                    {Object.keys(EXAMPLE_WORKFLOWS).length} templates across {Object.keys(TEMPLATE_CATEGORIES).length} categories
                </DropdownMenuLabel>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
