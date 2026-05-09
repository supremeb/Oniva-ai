/**
 * Shared Chat Components
 *
 * A comprehensive component library for AI chat interfaces.
 * Follows emilkowal animation standards with professional styling.
 *
 * Component Categories:
 * - primitives: Base animation and layout components
 * - streaming: Real-time content streaming components
 * - tools: Tool call visualization
 * - interactive: User interaction components
 * - data: Data visualization components
 * - status: Loading and notification components
 *
 * Legacy Components (existing):
 * - ChatEmptyState, ChatLoadingIndicator, ChatMessage, ChatErrorBoundary, ChatInputField
 */

// ============================================
// LEGACY CHAT COMPONENTS (existing)
// ============================================

export {
  ChatEmptyState,
  ChatEmptyStateCompact,
  type ChatEmptyStateProps,
} from './ChatEmptyState';

export {
  ChatLoadingIndicator,
  InlineLoadingIndicator,
  type ChatLoadingIndicatorProps,
} from './ChatLoadingIndicator';

export {
  ChatMessage,
  ChatMessageList,
  type ChatMessageProps,
  type ChatMessageListProps,
  type ChatMessageData,
  type LegacyMessage,
} from './ChatMessage';

export {
  ChatErrorBoundary,
  ChatError,
  type ChatErrorBoundaryProps,
  type ChatErrorProps,
} from './ChatErrorBoundary';

export {
  ChatInputField,
  type ChatInputFieldProps,
} from './ChatInputField';

// ============================================
// NEW AI CHAT UI COMPONENTS
// ============================================

// Main Message Renderer
export { MessageRenderer, type MessageRendererProps } from './MessageRenderer';

// Chain of Thought — unified thinking + tool timeline
export { ChainOfThought, type ChainOfThoughtProps } from './ChainOfThought';

// Primitives - Base animation and layout components
export {
  AnimatedPresence,
  type AnimatedPresenceProps,
  FadeSlide,
  type FadeSlideProps,
  SpringCard,
  type SpringCardProps,
  StaggerContainer,
  type StaggerContainerProps,
  StreamingReveal,
  type StreamingRevealProps,
  PulseLoader,
  type PulseLoaderProps,
  SkeletonBlock,
  type SkeletonBlockProps,
} from './primitives';

// Streaming - Real-time content streaming components
export {
  StreamingText,
  type StreamingTextProps,
  StreamingCursor,
  type StreamingCursorProps,
  StreamingCode,
  type StreamingCodeProps,
  StreamingProgress,
  type StreamingProgressProps,
} from './streaming';

// Tools - Tool call visualization
export {
  ToolCallHeader,
  ToolCallStatusBadge,
  type ToolCallHeaderProps,
  type ToolCallStatusBadgeProps,
  ToolCallProgress,
  ToolCallProgressInline,
  type ToolCallProgressProps,
  type ToolCallProgressInlineProps,
  type ToolCallStep,
  ToolCallResult,
  ToolCallResultCompact,
  type ToolCallResultProps,
  type ToolCallResultCompactProps,
  ToolCallError,
  ToolCallErrorInline,
  type ToolCallErrorProps,
  type ToolCallErrorInlineProps,
  ToolCallCard,
  ToolCallCardCompact,
  type ToolCallCardProps,
  type ToolCallCardCompactProps,
} from './tools';

// Interactive - User interaction components
export {
  ActionButton,
  type ActionButtonProps,
  ApprovalCard,
  type ApprovalCardProps,
  QuestionOption,
  type QuestionOptionProps,
  OtherInput,
  type OtherInputProps,
  QuestionProgress,
  type QuestionProgressProps,
  AgentQuestion,
  type AgentQuestionProps,
  type AgentQuestionOption,
  AgentQuestionGroup,
  type AgentQuestionGroupProps,
  type QuestionData,
  QuestionPrompt,
  type QuestionPromptProps,
  FormCard,
  type FormCardProps,
  type FormField,
  OAuthCard,
  type OAuthCardProps,
  OrgShareCard,
  type OrgShareCardProps,
} from './interactive';

// Data Visualization - Display structured data
export {
  DataTable,
  type DataTableProps,
  type DataTableColumn,
  ChartPreview,
  type ChartPreviewProps,
  type ChartType,
  SourceCard,
  type SourceCardProps,
  type SourceType,
  CitationBadge,
  type CitationBadgeProps,
  FilePreview,
  type FilePreviewProps,
  ImagePreview,
  type ImagePreviewProps,
  LinkPreview,
  type LinkPreviewProps,
  SQLPreview,
  type SQLPreviewProps,
  SQLResultTable,
  type SQLResultTableProps,
  type SQLResultColumn,
  JSONViewer,
  type JSONViewerProps,
  DiffViewer,
  type DiffViewerProps,
  type DiffLine,
  TimelineEvent,
  Timeline,
  type TimelineEventProps,
  type TimelineProps,
  type TimelineEventStatus,
  MetricCard,
  MetricGrid,
  type MetricCardProps,
  type MetricGridProps,
  type MetricTrend,
  StatusBadge,
  type StatusBadgeProps,
  type StatusType,
  type StatusSize,
} from './data';

// Status - Loading and notification components
export {
  ThinkingIndicator,
  type ThinkingIndicatorProps,
  type ThinkingVariant,
  StepIndicator,
  ProgressSteps,
  type StepIndicatorProps,
  type ProgressStepsProps,
  type Step,
  type StepStatus,
  ErrorMessage,
  SuccessMessage,
  WarningMessage,
  InfoMessage,
  type MessageAlertProps,
} from './status';
