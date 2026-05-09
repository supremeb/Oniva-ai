'use client';

import { memo, useState, useCallback, useEffect, useRef, type KeyboardEvent } from 'react';
import { cn } from '@/lib/utils';
import { Question, DotsThree, Check } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { StaggerContainer } from '../primitives/StaggerContainer';
import { QuestionOption } from './QuestionOption';
import { OtherInput } from './OtherInput';
import { QuestionProgress } from './QuestionProgress';
import { ActionButton } from './ActionButton';
import { transitions } from '@/lib/animation-utils';

export interface AgentQuestionOption {
  id: string;
  label: string;
  description?: string;
}

export interface AgentQuestionProps {
  /** Unique identifier */
  id: string;
  /** Short header/category label */
  header: string;
  /** Full question text */
  question: string;
  /** Available options */
  options: AgentQuestionOption[];
  /** Allow multiple selections */
  multiSelect?: boolean;
  /** Timeout in seconds (0 = no timeout) */
  timeout?: number;
  /** Default option ID to auto-select on timeout */
  defaultOptionId?: string;
  /** Include "Other" option for custom input */
  includeOther?: boolean;
  /** Current state */
  status?: 'pending' | 'answered' | 'expired';
  /** Previously selected values (for resuming) */
  initialSelection?: string[];
  /** Callback when answered */
  onAnswer?: (answer: { optionIds: string[]; otherValue?: string }) => void;
  /** Callback when timeout expires */
  onTimeout?: (defaultOptionId?: string) => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * AgentQuestion - Interactive question component for agent interviews
 *
 * Inspired by Tool UI's Question Flow component.
 * Features:
 * - Keyboard navigation (1-9 keys, arrow keys, Enter, Escape)
 * - "Other" option with inline text input
 * - Timeout countdown with urgency pulse
 * - Multi-select support
 * - Auto-select default option on timeout
 */
export const AgentQuestion = memo(function AgentQuestion({
  id,
  header,
  question,
  options,
  multiSelect = false,
  timeout = 0,
  defaultOptionId,
  includeOther = true,
  status = 'pending',
  initialSelection = [],
  onAnswer,
  onTimeout,
  className,
}: AgentQuestionProps) {
  // State
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set(initialSelection));
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [showOther, setShowOther] = useState(false);
  const [otherValue, setOtherValue] = useState('');
  const [timeRemaining, setTimeRemaining] = useState(timeout);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // All options including "Other"
  const allOptions = includeOther
    ? [...options, { id: '__other__', label: 'Other', description: 'Provide a custom response' }]
    : options;

  const isPending = status === 'pending';
  const hasSelection = selectedIds.size > 0 || (showOther && otherValue.trim());

  // Timeout countdown
  useEffect(() => {
    if (!isPending || timeout <= 0) return;

    const timer = setInterval(() => {
      setTimeRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [isPending, timeout]);

  // Handle timeout expiration
  useEffect(() => {
    if (timeRemaining === 0 && timeout > 0 && isPending) {
      if (defaultOptionId) {
        handleSubmit([defaultOptionId]);
      }
      onTimeout?.(defaultOptionId);
    }
  }, [timeRemaining, timeout, isPending, defaultOptionId, onTimeout]);

  // Focus management
  useEffect(() => {
    if (isPending) {
      containerRef.current?.focus();
    }
  }, [isPending]);

  // Handle option selection
  const handleSelect = useCallback(
    (optionId: string) => {
      if (!isPending) return;

      if (optionId === '__other__') {
        if (multiSelect) {
          setSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(optionId)) {
              next.delete(optionId);
              setShowOther(false);
            } else {
              next.add(optionId);
              setShowOther(true);
            }
            return next;
          });
        } else {
          setSelectedIds(new Set([optionId]));
          setShowOther(true);
        }
        return;
      }

      if (multiSelect) {
        setSelectedIds((prev) => {
          const next = new Set(prev);
          if (next.has(optionId)) {
            next.delete(optionId);
          } else {
            next.add(optionId);
          }
          // Clear "other" if a regular option is selected in single-select mode
          next.delete('__other__');
          setShowOther(false);
          return next;
        });
      } else {
        setSelectedIds(new Set([optionId]));
        setShowOther(false);
        // Auto-submit for single select (unless it's "Other")
        if (!multiSelect) {
          handleSubmit([optionId]);
        }
      }
    },
    [isPending, multiSelect]
  );

  // Handle submission
  const handleSubmit = useCallback(
    async (forceIds?: string[]) => {
      if (isSubmitting) return;

      const ids = forceIds || Array.from(selectedIds);
      const hasOther = ids.includes('__other__');
      const optionIds = ids.filter((id) => id !== '__other__');

      if (!hasOther && optionIds.length === 0) return;
      if (hasOther && !otherValue.trim()) return;

      setIsSubmitting(true);

      try {
        await onAnswer?.({
          optionIds,
          otherValue: hasOther ? otherValue.trim() : undefined,
        });
      } finally {
        setIsSubmitting(false);
      }
    },
    [selectedIds, otherValue, isSubmitting, onAnswer]
  );

  // Keyboard navigation
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!isPending || showOther) return;

      const { key } = e;

      // Number keys (1-9) for quick select
      if (key >= '1' && key <= '9') {
        const index = parseInt(key) - 1;
        if (index < allOptions.length) {
          e.preventDefault();
          setFocusedIndex(index);
          handleSelect(allOptions[index].id);
        }
        return;
      }

      switch (key) {
        case 'ArrowDown':
        case 'j':
          e.preventDefault();
          setFocusedIndex((prev) => Math.min(prev + 1, allOptions.length - 1));
          break;

        case 'ArrowUp':
        case 'k':
          e.preventDefault();
          setFocusedIndex((prev) => Math.max(prev - 1, 0));
          break;

        case 'Enter':
        case ' ':
          e.preventDefault();
          if (focusedIndex >= 0 && focusedIndex < allOptions.length) {
            handleSelect(allOptions[focusedIndex].id);
          }
          break;

        case 'Escape':
          e.preventDefault();
          if (multiSelect && hasSelection) {
            // Clear selection in multi-select
            setSelectedIds(new Set());
          }
          break;
      }
    },
    [isPending, showOther, allOptions, focusedIndex, handleSelect, multiSelect, hasSelection]
  );

  // Handle Other input submission
  const handleOtherSubmit = useCallback(
    (value: string) => {
      setOtherValue(value);
      handleSubmit([...Array.from(selectedIds), '__other__']);
    },
    [selectedIds, handleSubmit]
  );

  // Handle Other input cancel
  const handleOtherCancel = useCallback(() => {
    setShowOther(false);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.delete('__other__');
      return next;
    });
  }, []);

  return (
    <FadeSlide>
      <SpringCard
        ref={containerRef}
        variant="outline"
        tabIndex={isPending ? 0 : -1}
        onKeyDown={handleKeyDown}
        className={cn(
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          !isPending && 'opacity-80',
          className
        )}
      >
        {/* Header */}
        <div className="flex items-start gap-3 mb-4">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 shrink-0">
            <Question className="h-4 w-4 text-primary" weight="fill" />
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium text-primary uppercase tracking-wide">
                {header}
              </span>
              {status === 'answered' && (
                <span className="flex items-center gap-1 text-xs text-green-500">
                  <Check className="h-3 w-3" weight="bold" />
                  Answered
                </span>
              )}
              {status === 'expired' && (
                <span className="text-xs text-orange-500">Expired</span>
              )}
            </div>
            <p className="text-sm font-medium text-foreground">{question}</p>
          </div>
        </div>

        {/* Timeout progress */}
        {timeout > 0 && isPending && (
          <div className="mb-4">
            <QuestionProgress
              timeout={timeout}
              remaining={timeRemaining}
              urgencyThreshold={10}
              variant="linear"
            />
          </div>
        )}

        {/* Options */}
        {isPending && (
          <StaggerContainer
            staggerDelay={30}
            animation="fade-slide-up"
            className="space-y-2"
          >
            {allOptions.map((option, index) => (
              <QuestionOption
                key={option.id}
                ref={(el) => { optionRefs.current[index] = el; }}
                id={option.id}
                label={option.label}
                description={option.description}
                selected={selectedIds.has(option.id)}
                shortcut={index < 9 ? index + 1 : undefined}
                multiSelect={multiSelect}
                focused={focusedIndex === index}
                onSelect={() => {
                  setFocusedIndex(index);
                  handleSelect(option.id);
                }}
              />
            ))}
          </StaggerContainer>
        )}

        {/* Other input */}
        {showOther && (
          <div className="mt-3">
            <OtherInput
              expanded={showOther}
              value={otherValue}
              onChange={setOtherValue}
              onSubmit={handleOtherSubmit}
              onCancel={handleOtherCancel}
              placeholder="Type your custom response..."
            />
          </div>
        )}

        {/* Multi-select submit button */}
        {multiSelect && isPending && !showOther && hasSelection && (
          <div className="mt-4 pt-4 border-t border-border">
            <ActionButton
              variant="primary"
              size="sm"
              loading={isSubmitting}
              onClick={() => handleSubmit()}
              icon={!isSubmitting ? <Check className="h-3.5 w-3.5" weight="bold" /> : undefined}
            >
              Submit ({selectedIds.size} selected)
            </ActionButton>
          </div>
        )}

        {/* Answered state */}
        {status === 'answered' && (
          <div className="mt-3 pt-3 border-t border-border">
            <div className="flex flex-wrap gap-2">
              {Array.from(selectedIds)
                .filter((id) => id !== '__other__')
                .map((id) => {
                  const option = options.find((o) => o.id === id);
                  return option ? (
                    <span
                      key={id}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-sm text-primary"
                    >
                      <Check className="h-3 w-3" weight="bold" />
                      {option.label}
                    </span>
                  ) : null;
                })}
              {otherValue && (
                <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-sm text-primary">
                  <DotsThree className="h-3 w-3" weight="bold" />
                  {otherValue}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Keyboard hints */}
        {isPending && !showOther && (
          <div className="mt-4 pt-3 border-t border-border flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              <kbd className="px-1 py-0.5 rounded bg-muted font-mono">1-9</kbd> Quick select
            </span>
            <span>
              <kbd className="px-1 py-0.5 rounded bg-muted font-mono">↑↓</kbd> Navigate
            </span>
            <span>
              <kbd className="px-1 py-0.5 rounded bg-muted font-mono">Enter</kbd> Select
            </span>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { AgentQuestionProps as AgentQuestionComponentProps };
