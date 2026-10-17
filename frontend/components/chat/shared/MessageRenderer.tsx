'use client';

import { memo, useMemo } from 'react';
import { cn } from '@/lib/utils';
import type { MessagePart } from '@/lib/models';

// Import all component categories
import {
  StreamingText,
  StreamingCode,
  StreamingCursor,
} from './streaming';

import {
  ToolCallCard,
  ToolCallCardCompact,
} from './tools';

import {
  AgentQuestion,
  ApprovalCard,
  QuestionCard,
  QuestionPrompt,
  FormCard,
  OAuthCard,
  MemorySavedCard,
} from './interactive';

import {
  DataTable,
  ChartPreview,
  FilePreview,
  ImagePreview,
  LinkPreview,
  SQLPreview,
  SQLResultTable,
  JSONViewer,
  DiffViewer,
  TimelineEvent,
  Timeline,
  MetricCard,
  type ArtifactModalData,
} from './data';

import {
  ThinkingIndicator,
  StepIndicator,
  ErrorMessage,
  SuccessMessage,
  WarningMessage,
  InfoMessage,
} from './status';

import { StaggerContainer } from './primitives';

export interface MessageRendererProps {
  /** Message parts to render */
  parts: MessagePart[];
  /** Compact display mode */
  compact?: boolean;
  /** Enable animations */
  animated?: boolean;
  /** Stagger delay between parts */
  staggerDelay?: number;
  /** Callback handlers */
  handlers?: {
    onToolRetry?: (toolId: string) => void;
    onQuestionAnswer?: (questionId: string, answer: { optionIds: string[]; otherValue?: string }) => void | Promise<void>;
    onApprove?: (approvalId: string) => void | Promise<void>;
    onReject?: (approvalId: string) => void | Promise<void>;
    onFormSubmit?: (formId: string, values: Record<string, string>) => void;
    onOAuthAuthorize?: (oauthId: string) => void;
    onLinkClick?: (url: string) => void;
    onFileDownload?: (filename: string, url: string) => void;
    onOpenModal?: (data: ArtifactModalData) => void;
  };
  /** Additional CSS classes */
  className?: string;
}

/**
 * MessageRenderer - Render array of MessagePart components
 *
 * Central component that maps MessagePart types to their
 * corresponding visual components.
 *
 * Features:
 * - Type-safe part rendering
 * - Staggered animations
 * - Handler callbacks
 * - Compact mode support
 */
export const MessageRenderer = memo(function MessageRenderer({
  parts,
  compact = false,
  animated = true,
  staggerDelay = 30,
  handlers = {},
  className,
}: MessageRendererProps) {
  // Render individual part based on type
  const renderPart = (part: MessagePart, index: number) => {
    const key = `part-${index}-${part.type}`;

    switch (part.type) {
      // Text content
      case 'text':
        return (
          <StreamingText
            key={key}
            content={part.content}
            isStreaming={part.isStreaming}
            compact={compact}
          />
        );

      // Code blocks
      case 'code':
        return (
          <StreamingCode
            key={key}
            content={part.content}
            language={part.language}
            filename={part.filename}
          />
        );

      // Tool calls
      case 'tool_call':
        return compact ? (
          <ToolCallCardCompact
            key={key}
            toolName={part.toolName}
            status={part.status}
            durationMs={part.durationMs}
          />
        ) : (
          <ToolCallCard
            key={key}
            id={part.id}
            toolName={part.toolName}
            status={part.status}
            args={part.args}
            result={part.result}
            error={part.error}
            durationMs={part.durationMs}
            onRetry={handlers.onToolRetry ? () => handlers.onToolRetry?.(part.id) : undefined}
          />
        );

      // Agent questions
      case 'question':
        if (part.toolTaskId !== undefined) {
          // Engine `ask_user`: the durable question card.
          return (
            <QuestionCard
              key={key}
              id={part.id}
              question={part.question}
              options={part.options}
              multiSelect={part.multiSelect}
              allowFreeText={part.allowFreeText}
              status={part.status}
              answer={part.answer}
              expiresAt={part.expiresAt}
              readOnly={part.canDecide === false}
              onAnswer={handlers.onQuestionAnswer ? (answer) => handlers.onQuestionAnswer?.(part.id, answer) : undefined}
            />
          );
        }
        return (
          <AgentQuestion
            key={key}
            id={part.id}
            header={part.header}
            question={part.question}
            options={part.options}
            multiSelect={part.multiSelect}
            timeout={part.timeout}
            defaultOptionId={part.defaultOptionId}
            onAnswer={(answer) => handlers.onQuestionAnswer?.(part.id, answer)}
          />
        );

      // Approval cards
      case 'approval':
        return (
          <ApprovalCard
            key={key}
            id={part.id}
            title={part.title}
            description={part.description}
            riskLevel={part.riskLevel}
            items={part.items}
            argumentsHash={part.argumentsHash}
            status={part.status}
            toolName={part.toolName}
            expiresAt={part.expiresAt}
            note={part.note}
            readOnly={part.canDecide === false}
            resolvedAsLine={part.toolTaskId !== undefined}
            onApprove={handlers.onApprove ? () => handlers.onApprove?.(part.id) : undefined}
            onReject={handlers.onReject ? () => handlers.onReject?.(part.id) : undefined}
          />
        );

      // Form cards
      case 'form':
        return (
          <FormCard
            key={key}
            id={part.id}
            title={part.title}
            description={part.description}
            fields={part.fields}
            onSubmit={(values) => handlers.onFormSubmit?.(part.id, values)}
          />
        );

      // Free-form input
      case 'input':
        return (
          <QuestionPrompt
            key={key}
            id={part.id}
            prompt={part.prompt}
            placeholder={part.placeholder}
            inputType={part.inputType}
            required={part.required}
            onAnswer={(value) => handlers.onQuestionAnswer?.(part.id, { optionIds: [], otherValue: value })}
          />
        );

      // OAuth cards
      case 'oauth':
        return (
          <OAuthCard
            key={key}
            id={part.id}
            serviceName={part.serviceName}
            description={part.description}
            scopes={part.scopes}
            status={part.status}
            authUrl={part.authUrl}
            accountInfo={part.accountInfo}
            onAuthorize={handlers.onOAuthAuthorize ? () => handlers.onOAuthAuthorize?.(part.id) : undefined}
          />
        );

      // Data tables
      case 'table':
        return (
          <DataTable
            key={key}
            columns={part.columns}
            data={part.data}
            title={part.title}
            sortable={part.sortable}
            searchable={part.searchable}
            compact={compact}
          />
        );

      // Charts
      case 'chart':
        return (
          <ChartPreview
            key={key}
            type={part.chartType}
            title={part.title}
            description={part.description}
            imageUrl={part.imageUrl}
            svgContent={part.svgContent}
          />
        );

      // Sources/citations — rendered as aggregated SourcesPill by ChainOfThought, skip here
      case 'source':
        return null;

      // File previews
      case 'file':
        return (
          <FilePreview
            key={key}
            filename={part.filename}
            size={part.size}
            type={part.fileType}
            downloadUrl={part.downloadUrl}
            content={part.content}
            compact={compact}
            onOpenModal={handlers.onOpenModal}
            title={part.title}
            description={part.description}
            previewUrl={part.previewUrl}
            artifactType={part.artifactType}
            artifactUri={part.artifactUri}
            tags={part.tags}
            pageCount={part.pageCount}
          />
        );

      // Image previews
      case 'image':
        return (
          <ImagePreview
            key={key}
            src={part.src}
            alt={part.alt}
            caption={part.caption}
            aspectRatio={part.aspectRatio}
          />
        );

      // Link previews
      case 'link':
        return (
          <LinkPreview
            key={key}
            url={part.url}
            title={part.title}
            description={part.description}
            imageUrl={part.imageUrl}
            faviconUrl={part.faviconUrl}
            siteName={part.siteName}
            compact={compact}
            onClick={() => handlers.onLinkClick?.(part.url)}
          />
        );

      // SQL queries
      case 'sql':
        return (
          <SQLPreview
            key={key}
            query={part.query}
            database={part.database}
            status={part.status}
            executionTime={part.executionTime}
            rowCount={part.rowCount}
            error={part.error}
            compact={compact}
          />
        );

      // SQL results
      case 'sql_result':
        return (
          <SQLResultTable
            key={key}
            columns={part.columns}
            rows={part.rows}
            executionTime={part.executionTime}
            totalRows={part.totalRows}
            compact={compact}
          />
        );

      // JSON data
      case 'json':
        return (
          <JSONViewer
            key={key}
            data={part.data}
            title={part.title}
            defaultCollapsedDepth={part.defaultCollapsedDepth}
            compact={compact}
          />
        );

      // Diff/changes
      case 'diff':
        return (
          <DiffViewer
            key={key}
            diff={part.diff}
            lines={part.lines}
            title={part.title}
            oldFileName={part.oldFileName}
            newFileName={part.newFileName}
            compact={compact}
          />
        );

      // Timeline events
      case 'timeline':
        return (
          <Timeline key={key} compact={compact}>
            {part.events.map((event, idx) => (
              <TimelineEvent
                key={`${key}-event-${idx}`}
                title={event.title}
                description={event.description}
                status={event.status}
                timestamp={event.timestamp}
                isLast={idx === part.events.length - 1}
                compact={compact}
              />
            ))}
          </Timeline>
        );

      // Metrics
      case 'metric':
        return (
          <MetricCard
            key={key}
            label={part.label}
            value={part.value}
            change={part.change}
            trend={part.trend}
            prefix={part.prefix}
            suffix={part.suffix}
            context={part.context}
            compact={compact}
          />
        );

      // Progress/steps
      case 'steps':
        return (
          <StepIndicator
            key={key}
            steps={part.steps}
            orientation={part.orientation}
            compact={compact}
          />
        );

      // Thinking/loading
      case 'thinking':
        return (
          <ThinkingIndicator
            key={key}
            variant={part.variant}
            message={part.message}
            stage={part.stage}
            showElapsed={part.showElapsed}
            thinkingContent={part.thinkingContent}
            isThinking={part.isThinking}
          />
        );

      // Status messages
      case 'error':
        return (
          <ErrorMessage
            key={key}
            message={part.message}
            title={part.title}
            details={part.details}
            onRetry={part.retryable ? () => handlers.onToolRetry?.(part.id || '') : undefined}
            compact={compact}
          />
        );

      case 'success':
        return (
          <SuccessMessage
            key={key}
            message={part.message}
            title={part.title}
            details={part.details}
            compact={compact}
          />
        );

      case 'warning':
        return (
          <WarningMessage
            key={key}
            message={part.message}
            title={part.title}
            details={part.details}
            compact={compact}
          />
        );

      case 'info':
        return (
          <InfoMessage
            key={key}
            message={part.message}
            title={part.title}
            details={part.details}
            compact={compact}
          />
        );

      // Memory saved card
      case 'memory_saved':
        return (
          <MemorySavedCard
            key={key}
            memoryId={part.memoryId}
            snippet={part.snippet}
            agentName={part.agentName}
            actionType={part.actionType}
          />
        );

      default:
        // Unknown type - render as text fallback
        console.warn(`Unknown message part type: ${(part as MessagePart).type}`);
        return null;
    }
  };

  // Memoize rendered parts
  const renderedParts = useMemo(
    () => parts.map((part, index) => renderPart(part, index)).filter(Boolean),
    [parts, compact, handlers]
  );

  if (parts.length === 0) {
    return null;
  }

  if (animated) {
    return (
      <StaggerContainer
        staggerDelay={staggerDelay}
        animation="fade-slide-up"
        className={cn('space-y-3', className)}
      >
        {renderedParts}
      </StaggerContainer>
    );
  }

  return (
    <div className={cn('space-y-3', className)}>
      {renderedParts}
    </div>
  );
});
