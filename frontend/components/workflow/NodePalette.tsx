'use client';

import { Database, Brain, Export, MagnifyingGlass, Code, Globe, GitBranch, ArrowsClockwise, TextT, FunnelSimple, ChatTeardropText, Play, TrendUp, ShieldCheck, BookOpen, Toolbox, Plugs, Robot, Timer, Equals, ListBullets, Note, FileText, Crosshair, Scan, Bell, Link, TreeStructure, UserCircle, CalendarBlank, Table } from '@phosphor-icons/react';

export const nodeTypes = [
    // Agents & Integrations
    { type: 'agent', label: 'Agent', icon: Robot, color: 'text-indigo-600', bg: 'bg-indigo-50', category: 'Agents & Integrations' },
    { type: 'skill', label: 'Skill', icon: Toolbox, color: 'text-green-600', bg: 'bg-green-50', category: 'Agents & Integrations' },
    { type: 'connector', label: 'Connector', icon: Plugs, color: 'text-teal-600', bg: 'bg-teal-50', category: 'Agents & Integrations' },

    // Financial Agents
    { type: 'compliance', label: 'Compliance', icon: ShieldCheck, color: 'text-purple-600', bg: 'bg-purple-50', category: 'Financial Agents' },

    // Basic nodes
    { type: 'start', label: 'Start', icon: Play, color: 'text-gray-600', bg: 'bg-gray-50', category: 'Basic' },
    { type: 'llm', label: 'LLM', icon: Brain, color: 'text-purple-600', bg: 'bg-purple-50', category: 'Basic' },
    { type: 'knowledge', label: 'Knowledge Retrieval', icon: MagnifyingGlass, color: 'text-green-600', bg: 'bg-green-50', category: 'Basic' },
    { type: 'output', label: 'End', icon: Export, color: 'text-teal-600', bg: 'bg-teal-50', category: 'Basic' },

    // Logic nodes
    { type: 'ifelse', label: 'IF/ELSE', icon: GitBranch, color: 'text-yellow-600', bg: 'bg-yellow-50', category: 'Logic' },
    { type: 'iteration', label: 'Iteration', icon: ArrowsClockwise, color: 'text-indigo-600', bg: 'bg-indigo-50', category: 'Logic' },
    { type: 'classifier', label: 'Question Classifier', icon: ChatTeardropText, color: 'text-violet-600', bg: 'bg-violet-50', category: 'Logic' },
    { type: 'wait', label: 'Wait/Delay', icon: Timer, color: 'text-indigo-600', bg: 'bg-indigo-50', category: 'Logic' },
    { type: 'subworkflow', label: 'Sub-Workflow', icon: TreeStructure, color: 'text-indigo-600', bg: 'bg-indigo-50', category: 'Logic' },
    { type: 'human_in_loop', label: 'Human Approval', icon: UserCircle, color: 'text-red-600', bg: 'bg-red-50', category: 'Logic' },

    // Transform nodes
    { type: 'code', label: 'Code', icon: Code, color: 'text-blue-600', bg: 'bg-blue-50', category: 'Transform' },
    { type: 'template', label: 'Template', icon: TextT, color: 'text-pink-600', bg: 'bg-pink-50', category: 'Transform' },
    { type: 'aggregator', label: 'Variable Aggregator', icon: FunnelSimple, color: 'text-cyan-600', bg: 'bg-cyan-50', category: 'Transform' },
    { type: 'variable_assigner', label: 'Variable Assigner', icon: Equals, color: 'text-cyan-600', bg: 'bg-cyan-50', category: 'Transform' },
    { type: 'list_operator', label: 'List Operator', icon: ListBullets, color: 'text-cyan-600', bg: 'bg-cyan-50', category: 'Transform' },
    { type: 'parameter_extractor', label: 'Parameter Extractor', icon: Crosshair, color: 'text-violet-600', bg: 'bg-violet-50', category: 'Transform' },

    // Data Processing nodes
    { type: 'document_extractor', label: 'Document Extractor', icon: FileText, color: 'text-green-600', bg: 'bg-green-50', category: 'Data Processing' },
    { type: 'ocr', label: 'OCR', icon: Scan, color: 'text-sky-600', bg: 'bg-sky-50', category: 'Data Processing' },
    { type: 'spreadsheet', label: 'Spreadsheet', icon: Table, color: 'text-emerald-600', bg: 'bg-emerald-50', category: 'Data Processing' },

    // Utilities
    { type: 'http', label: 'HTTP Request', icon: Globe, color: 'text-orange-600', bg: 'bg-orange-50', category: 'Utilities' },
    { type: 'notification', label: 'Notification', icon: Bell, color: 'text-orange-600', bg: 'bg-orange-50', category: 'Utilities' },
    { type: 'webhook_trigger', label: 'Webhook Trigger', icon: Link, color: 'text-orange-600', bg: 'bg-orange-50', category: 'Utilities' },
    { type: 'notes', label: 'Notes', icon: Note, color: 'text-yellow-600', bg: 'bg-yellow-50', category: 'Utilities' },
];

const categories = ['Agents & Integrations', 'Financial Agents', 'Basic', 'Logic', 'Transform', 'Data Processing', 'Utilities'];

export function NodePalette() {
    const onDragStart = (event: React.DragEvent, nodeType: string) => {
        event.dataTransfer.setData('application/reactflow', nodeType);
        event.dataTransfer.effectAllowed = 'move';
    };

    return (
        <aside className="w-56 border-r bg-background flex flex-col shrink-0 h-full overflow-y-auto">
            <div className="p-4 border-b">
                <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                    Blocks
                </h3>
            </div>

            <div className="p-3 space-y-4 flex-1 overflow-y-auto">
                {categories.map((category) => {
                    const categoryNodes = nodeTypes.filter((n) => n.category === category);
                    return (
                        <div key={category}>
                            <h4 className="text-xs font-medium text-muted-foreground mb-2 uppercase">
                                {category}
                            </h4>
                            <div className="space-y-1.5">
                                {categoryNodes.map(({ type, label, icon: Icon, color, bg }) => (
                                    <div
                                        key={type}
                                        className={`flex items-center gap-2 p-2.5 border rounded-lg cursor-grab hover:border-primary hover:shadow-sm transition-all ${bg}`}
                                        draggable
                                        onDragStart={(e) => onDragStart(e, type)}
                                    >
                                        <div className={`w-5 h-5 rounded flex items-center justify-center ${color}`}>
                                            <Icon className="h-3.5 w-3.5" weight="duotone" />
                                        </div>
                                        <span className="text-xs font-medium">{label}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    );
                })}
            </div>

            <div className="p-3 border-t text-[10px] text-muted-foreground">
                <p>Drag nodes to canvas to build your workflow</p>
            </div>
        </aside>
    );
}
