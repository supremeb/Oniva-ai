'use client';

import React from 'react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  FileText,
  FileCode,
  Table,
  CheckCircle,
  Clock,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import ReactMarkdown from 'react-markdown';

/**
 * Output from a workflow Output node.
 * Part of Phase 3: User Experience improvements.
 */
export interface WorkflowOutputNode {
  node_id: string;
  node_name: string;
  output_type: 'text' | 'json' | 'markdown' | 'table';
  content: unknown;
  timestamp: string;
}

interface OutputDisplayProps {
  outputs: WorkflowOutputNode[];
  className?: string;
  compact?: boolean;
}

/**
 * Displays structured outputs from workflow execution.
 *
 * Renders different output types appropriately:
 * - text: Plain text with whitespace preservation
 * - json: Formatted JSON with syntax highlighting
 * - markdown: Rendered markdown content
 * - table: HTML table from markdown or CSV format
 */
export function OutputDisplay({ outputs, className, compact = false }: OutputDisplayProps) {
  if (outputs.length === 0) {
    return (
      <div className={cn(
        "text-muted-foreground text-sm p-4 text-center",
        className
      )}>
        No outputs generated. Workflow may not have any Output nodes.
      </div>
    );
  }

  return (
    <div className={cn("space-y-4", className)}>
      {outputs.map((output) => (
        <OutputCard key={output.node_id} output={output} compact={compact} />
      ))}
    </div>
  );
}

interface OutputCardProps {
  output: WorkflowOutputNode;
  compact?: boolean;
}

function OutputCard({ output, compact }: OutputCardProps) {
  const formattedTime = formatTimestamp(output.timestamp);

  return (
    <Card className={cn("overflow-hidden", compact ? "p-3" : "p-4")}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <OutputTypeIcon type={output.output_type} />
          <span className="font-medium text-foreground">{output.node_name}</span>
          <Badge variant="secondary" className="text-[10px] px-1.5">
            {output.output_type}
          </Badge>
        </div>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <Clock className="w-3 h-3" />
          <span>{formattedTime}</span>
          <CheckCircle className="w-3 h-3 text-green-500 ml-1" />
        </div>
      </div>

      {/* Content */}
      <div className="output-content">
        <OutputContent output={output} />
      </div>
    </Card>
  );
}

function OutputTypeIcon({ type }: { type: string }) {
  const iconClass = "w-4 h-4";

  switch (type) {
    case 'json':
      return <FileCode className={iconClass} weight="duotone" />;
    case 'markdown':
      return <FileText className={iconClass} weight="duotone" />;
    case 'table':
      return <Table className={iconClass} weight="duotone" />;
    default:
      return <FileText className={iconClass} weight="duotone" />;
  }
}

function OutputContent({ output }: { output: WorkflowOutputNode }) {
  const { output_type, content } = output;

  switch (output_type) {
    case 'json':
      return <JsonOutput content={content} />;
    case 'markdown':
      return <MarkdownOutput content={String(content)} />;
    case 'table':
      return <TableOutput content={String(content)} />;
    default:
      return <TextOutput content={String(content)} />;
  }
}

function TextOutput({ content }: { content: string }) {
  return (
    <div className="whitespace-pre-wrap text-sm leading-relaxed">
      {content}
    </div>
  );
}

function JsonOutput({ content }: { content: unknown }) {
  const formatted = typeof content === 'string'
    ? content
    : JSON.stringify(content, null, 2);

  return (
    <ScrollArea className="max-h-[400px]">
      <pre className="bg-muted p-3 rounded-md overflow-x-auto text-sm font-mono">
        <code>{formatted}</code>
      </pre>
    </ScrollArea>
  );
}

function MarkdownOutput({ content }: { content: string }) {
  return (
    <ScrollArea className="max-h-[500px]">
      <div className="prose prose-sm max-w-none dark:prose-invert prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5 prose-code:bg-muted prose-code:px-1 prose-code:rounded prose-pre:bg-muted">
        <ReactMarkdown>{content}</ReactMarkdown>
      </div>
    </ScrollArea>
  );
}

function TableOutput({ content }: { content: string }) {
  // Parse simple markdown tables or CSV
  const lines = content.split('\n').filter(line => line.trim());

  if (lines.length < 2) {
    // Not enough lines for a table, render as text
    return <TextOutput content={content} />;
  }

  // Check if it's a markdown table (has pipe characters)
  const isMarkdownTable = lines[0].includes('|');

  if (isMarkdownTable) {
    return <MarkdownTableOutput lines={lines} />;
  }

  // Try to parse as CSV/TSV
  const delimiter = lines[0].includes('\t') ? '\t' : ',';
  return <DelimitedTableOutput lines={lines} delimiter={delimiter} />;
}

function MarkdownTableOutput({ lines }: { lines: string[] }) {
  // Parse markdown table format: | header1 | header2 |
  const parseRow = (line: string) =>
    line.split('|').map(cell => cell.trim()).filter(Boolean);

  const headers = parseRow(lines[0]);

  // Skip the separator line (index 1) which contains dashes
  const dataLines = lines.slice(2);
  const rows = dataLines.map(parseRow);

  return (
    <ScrollArea className="max-h-[400px]">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b bg-muted/50">
            {headers.map((header, i) => (
              <th key={i} className="text-left p-2 font-medium">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b last:border-0 hover:bg-muted/30">
              {row.map((cell, j) => (
                <td key={j} className="p-2">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollArea>
  );
}

function DelimitedTableOutput({ lines, delimiter }: { lines: string[]; delimiter: string }) {
  const parseRow = (line: string) => line.split(delimiter).map(cell => cell.trim());

  const headers = parseRow(lines[0]);
  const rows = lines.slice(1).map(parseRow);

  return (
    <ScrollArea className="max-h-[400px]">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b bg-muted/50">
            {headers.map((header, i) => (
              <th key={i} className="text-left p-2 font-medium">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b last:border-0 hover:bg-muted/30">
              {row.map((cell, j) => (
                <td key={j} className="p-2">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollArea>
  );
}

function formatTimestamp(timestamp: string): string {
  try {
    const date = new Date(timestamp);
    return date.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return timestamp;
  }
}

/**
 * Compact version of OutputDisplay for embedding in chat messages.
 */
export function OutputDisplayCompact({ outputs }: { outputs: WorkflowOutputNode[] }) {
  return <OutputDisplay outputs={outputs} compact />;
}

export default OutputDisplay;
