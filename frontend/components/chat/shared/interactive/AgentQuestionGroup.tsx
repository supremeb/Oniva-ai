'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { ListNumbers, Check, CaretDown, CaretRight } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { StaggerContainer } from '../primitives/StaggerContainer';
import { AgentQuestion, type AgentQuestionProps, type AgentQuestionOption } from './AgentQuestion';
import { transitions } from '@/lib/animation-utils';

export interface QuestionData {
  id: string;
  header: string;
  question: string;
  options: AgentQuestionOption[];
  multiSelect?: boolean;
  timeout?: number;
  defaultOptionId?: string;
  includeOther?: boolean;
}

export interface AgentQuestionGroupProps {
  /** Unique identifier */
  id: string;
  /** Group title */
  title?: string;
  /** Group description */
  description?: string;
  /** Questions in the group */
  questions: QuestionData[];
  /** Sequential mode - show one question at a time */
  sequential?: boolean;
  /** Whether group is collapsible */
  collapsible?: boolean;
  /** Default collapsed state */
  defaultCollapsed?: boolean;
  /** Callback when a question is answered */
  onQuestionAnswer?: (questionId: string, answer: { optionIds: string[]; otherValue?: string }) => void;
  /** Callback when all questions are answered */
  onComplete?: (answers: Record<string, { optionIds: string[]; otherValue?: string }>) => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * AgentQuestionGroup - Container for multiple agent questions
 *
 * Features:
 * - Sequential or all-at-once display
 * - Progress tracking
 * - Collapsible group
 * - Completion callback
 */
export const AgentQuestionGroup = memo(function AgentQuestionGroup({
  id,
  title,
  description,
  questions,
  sequential = false,
  collapsible = true,
  defaultCollapsed = false,
  onQuestionAnswer,
  onComplete,
  className,
}: AgentQuestionGroupProps) {
  const [answers, setAnswers] = useState<Record<string, { optionIds: string[]; otherValue?: string }>>({});
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);

  const answeredCount = Object.keys(answers).length;
  const totalCount = questions.length;
  const allAnswered = answeredCount === totalCount;

  // Handle question answer
  const handleAnswer = useCallback(
    (questionId: string, answer: { optionIds: string[]; otherValue?: string }) => {
      setAnswers((prev) => {
        const next = { ...prev, [questionId]: answer };

        // Check if all questions are answered
        if (Object.keys(next).length === totalCount) {
          onComplete?.(next);
        }

        return next;
      });

      onQuestionAnswer?.(questionId, answer);

      // Move to next question in sequential mode
      if (sequential && currentIndex < totalCount - 1) {
        setCurrentIndex((prev) => prev + 1);
      }
    },
    [currentIndex, totalCount, sequential, onQuestionAnswer, onComplete]
  );

  // Get question status
  const getQuestionStatus = (questionId: string): 'pending' | 'answered' | 'expired' => {
    return answers[questionId] ? 'answered' : 'pending';
  };

  // Determine which questions to show
  const visibleQuestions = sequential
    ? questions.slice(0, currentIndex + 1)
    : questions;

  return (
    <FadeSlide className={className}>
      <div className="space-y-3">
        {/* Group header */}
        {(title || collapsible) && (
          <div
            className={cn(
              'flex items-center gap-3 px-1',
              collapsible && 'cursor-pointer',
              transitions.colors
            )}
            onClick={collapsible ? () => setIsCollapsed((prev) => !prev) : undefined}
          >
            {/* Collapse toggle */}
            {collapsible && (
              <button
                className={cn(
                  'p-0.5 rounded hover:bg-muted shrink-0',
                  transitions.colors
                )}
                aria-expanded={!isCollapsed}
              >
                {isCollapsed ? (
                  <CaretRight className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <CaretDown className="h-4 w-4 text-muted-foreground" />
                )}
              </button>
            )}

            {/* Icon */}
            <div className="flex items-center justify-center w-6 h-6 rounded bg-muted shrink-0">
              {allAnswered ? (
                <Check className="h-3.5 w-3.5 text-green-500" weight="bold" />
              ) : (
                <ListNumbers className="h-3.5 w-3.5 text-muted-foreground" />
              )}
            </div>

            {/* Title and progress */}
            <div className="flex-1 min-w-0">
              {title && (
                <h4 className="font-medium text-sm">{title}</h4>
              )}
              {description && !isCollapsed && (
                <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
              )}
            </div>

            {/* Progress indicator */}
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-xs text-muted-foreground">
                {answeredCount}/{totalCount}
              </span>
              <div className="w-16 h-1.5 rounded-full bg-muted overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full transition-all duration-300',
                    allAnswered ? 'bg-green-500' : 'bg-primary'
                  )}
                  style={{ width: `${(answeredCount / totalCount) * 100}%` }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Questions */}
        {!isCollapsed && (
          <StaggerContainer
            staggerDelay={50}
            animation="fade-slide-up"
            className="space-y-3"
          >
            {visibleQuestions.map((q, index) => (
              <AgentQuestion
                key={q.id}
                id={q.id}
                header={q.header}
                question={q.question}
                options={q.options}
                multiSelect={q.multiSelect}
                timeout={q.timeout}
                defaultOptionId={q.defaultOptionId}
                includeOther={q.includeOther}
                status={getQuestionStatus(q.id)}
                onAnswer={(answer) => handleAnswer(q.id, answer)}
              />
            ))}

            {/* Sequential mode: show "more questions" indicator */}
            {sequential && currentIndex < totalCount - 1 && (
              <div className="flex items-center justify-center py-2">
                <span className="text-xs text-muted-foreground">
                  {totalCount - currentIndex - 1} more {totalCount - currentIndex - 1 === 1 ? 'question' : 'questions'} remaining
                </span>
              </div>
            )}
          </StaggerContainer>
        )}

        {/* Collapsed summary */}
        {isCollapsed && answeredCount > 0 && (
          <div className="px-1 text-xs text-muted-foreground">
            {answeredCount} of {totalCount} questions answered
          </div>
        )}
      </div>
    </FadeSlide>
  );
});

export type { AgentQuestionGroupProps as AgentQuestionGroupComponentProps };
