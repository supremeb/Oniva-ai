'use client';

import { memo } from 'react';
import {
  Robot,
  ChatCircleDots,
  FlowArrow,
  Lightning,
  Brain,
} from '@phosphor-icons/react';
import type { Icon } from '@phosphor-icons/react';
import { EmptyState } from '@/components/ui/empty-state';
import { cn } from '@/lib/utils';

export interface ChatEmptyStateProps {
  /** Title displayed in the empty state */
  title?: string;
  /** Description text */
  description?: string;
  /** Custom icon to display */
  icon?: React.ReactNode;
  /** Additional CSS classes */
  className?: string;
  /** Context type for customizing default messages */
  context?: 'chat' | 'workflow' | 'agent';
  /** Suggested prompts to show */
  suggestions?: string[];
  /** Callback when a suggestion is clicked */
  onSuggestionClick?: (suggestion: string) => void;
}

interface ContextPreset {
  title: string;
  description: string;
  icon: Icon;
  gradient: string;
  iconColor: string;
  suggestions: string[];
}

const contextContent: Record<'chat' | 'workflow' | 'agent', ContextPreset> = {
  chat: {
    title: 'Start a Conversation',
    description: 'Select an agent and ask questions about financial data, compliance, or research.',
    icon: ChatCircleDots,
    gradient: 'from-violet-500/20 to-indigo-500/20',
    iconColor: 'text-violet-600',
    suggestions: [
      'Analyze the performance of AAPL stock',
      'Check compliance for a new trade',
      'Research recent market trends',
    ],
  },
  workflow: {
    title: 'Test Your Workflow',
    description: 'Send a message to execute and test your workflow configuration.',
    icon: FlowArrow,
    gradient: 'from-blue-500/20 to-cyan-500/20',
    iconColor: 'text-blue-600',
    suggestions: [
      'Run the workflow with sample data',
      'Execute a compliance check',
      'Process the latest financial data',
    ],
  },
  agent: {
    title: 'Chat with Agent',
    description: 'This agent is ready to help with your financial analysis tasks.',
    icon: Robot,
    gradient: 'from-emerald-500/20 to-teal-500/20',
    iconColor: 'text-emerald-600',
    suggestions: [
      'What can you help me with?',
      'Show me your capabilities',
      'Analyze current market conditions',
    ],
  },
};

const FeaturePill = memo(function FeaturePill({
  icon: Icon,
  label,
}: {
  icon: Icon;
  label: string;
}) {
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-muted/50 text-xs text-muted-foreground">
      <Icon className="h-3 w-3" weight="duotone" />
      {label}
    </div>
  );
});

export const ChatEmptyState = memo(function ChatEmptyState({
  title,
  description,
  icon,
  className,
  context = 'chat',
  suggestions,
  onSuggestionClick,
}: ChatEmptyStateProps) {
  const content = contextContent[context];
  const Icon = content.icon;
  const displaySuggestions = suggestions ?? content.suggestions;

  return (
    <EmptyState
      size="lg"
      iconTreatment="gradient"
      gradientClassName={content.gradient}
      title={title ?? content.title}
      description={description ?? content.description}
      icon={
        icon ?? (
          <Icon className={cn('h-10 w-10', content.iconColor)} weight="duotone" />
        )
      }
      pills={
        <>
          <FeaturePill icon={Lightning} label="Real-time" />
          <FeaturePill icon={Brain} label="AI-Powered" />
          <FeaturePill icon={Robot} label="Automated" />
        </>
      }
      suggestions={onSuggestionClick ? displaySuggestions : undefined}
      onSuggestionClick={onSuggestionClick}
      className={className}
    />
  );
});

/**
 * Minimal empty state for compact chat panels.
 * Delegates to the generic primitive with `size="sm"` plus chat-specific styling.
 */
export const ChatEmptyStateCompact = memo(function ChatEmptyStateCompact({
  context = 'chat',
  onSuggestionClick,
}: {
  context?: 'chat' | 'workflow' | 'agent';
  onSuggestionClick?: (suggestion: string) => void;
}) {
  const content = contextContent[context];
  const Icon = content.icon;

  return (
    <EmptyState
      size="sm"
      iconTreatment="gradient"
      gradientClassName={content.gradient}
      title={content.title}
      description={content.description}
      icon={
        <Icon className={cn('h-6 w-6', content.iconColor)} weight="duotone" />
      }
      action={
        onSuggestionClick && (
          <button
            type="button"
            onClick={() => onSuggestionClick(content.suggestions[0])}
            className={cn(
              'text-xs px-3 py-1.5 rounded-lg',
              'bg-primary/10 text-primary hover:bg-primary/20',
              'transition-colors duration-200',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
            )}
          >
            Get started
          </button>
        )
      }
    />
  );
});
