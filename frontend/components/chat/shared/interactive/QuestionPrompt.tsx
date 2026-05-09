'use client';

import { memo, useState, useCallback, useRef, useEffect, type KeyboardEvent } from 'react';
import { cn } from '@/lib/utils';
import { ChatText, PaperPlaneTilt } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface QuestionPromptProps {
  /** Unique identifier */
  id: string;
  /** Prompt text/question */
  prompt: string;
  /** Placeholder for input */
  placeholder?: string;
  /** Whether input is required */
  required?: boolean;
  /** Input type */
  inputType?: 'text' | 'number' | 'email' | 'url' | 'password';
  /** Maximum input length */
  maxLength?: number;
  /** Minimum input length */
  minLength?: number;
  /** Validation pattern (regex) */
  pattern?: string;
  /** Current state */
  status?: 'pending' | 'answered' | 'error';
  /** Error message */
  error?: string;
  /** Initial value */
  initialValue?: string;
  /** Auto-focus input */
  autoFocus?: boolean;
  /** Callback when answered */
  onAnswer?: (value: string) => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * QuestionPrompt - Free-form text input for agent questions
 *
 * Features:
 * - Various input types
 * - Validation support
 * - Error states
 * - Auto-focus
 * - Enter to submit
 */
export const QuestionPrompt = memo(function QuestionPrompt({
  id,
  prompt,
  placeholder = 'Type your answer...',
  required = false,
  inputType = 'text',
  maxLength,
  minLength,
  pattern,
  status = 'pending',
  error,
  initialValue = '',
  autoFocus = true,
  onAnswer,
  className,
}: QuestionPromptProps) {
  const [value, setValue] = useState(initialValue);
  const [localError, setLocalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const isPending = status === 'pending';
  const displayError = error || localError;

  // Auto-focus
  useEffect(() => {
    if (autoFocus && isPending && inputRef.current) {
      const timer = setTimeout(() => {
        inputRef.current?.focus();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [autoFocus, isPending]);

  // Validate input
  const validate = useCallback(
    (val: string): string | null => {
      if (required && !val.trim()) {
        return 'This field is required';
      }
      if (minLength && val.length < minLength) {
        return `Minimum ${minLength} characters required`;
      }
      if (maxLength && val.length > maxLength) {
        return `Maximum ${maxLength} characters allowed`;
      }
      if (pattern && !new RegExp(pattern).test(val)) {
        return 'Invalid format';
      }
      if (inputType === 'email' && val && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val)) {
        return 'Invalid email address';
      }
      if (inputType === 'url' && val) {
        try {
          new URL(val);
        } catch {
          return 'Invalid URL';
        }
      }
      return null;
    },
    [required, minLength, maxLength, pattern, inputType]
  );

  // Handle change
  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newValue = e.target.value;
      setValue(newValue);
      setLocalError(null);
    },
    []
  );

  // Handle submit
  const handleSubmit = useCallback(async () => {
    if (isSubmitting || !isPending) return;

    const validationError = validate(value);
    if (validationError) {
      setLocalError(validationError);
      return;
    }

    setIsSubmitting(true);
    try {
      await onAnswer?.(value.trim());
    } finally {
      setIsSubmitting(false);
    }
  }, [value, isSubmitting, isPending, validate, onAnswer]);

  // Handle key press
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit]
  );

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        className={cn(
          status === 'error' && 'border-destructive/30',
          className
        )}
      >
        {/* Prompt */}
        <div className="flex items-start gap-3 mb-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 shrink-0">
            <ChatText className="h-4 w-4 text-primary" weight="fill" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-medium text-foreground">
              {prompt}
              {required && <span className="text-destructive ml-1">*</span>}
            </p>
          </div>
        </div>

        {/* Input */}
        {isPending && (
          <div className="space-y-2">
            <div
              className={cn(
                'flex items-center gap-2 rounded-lg border bg-background overflow-hidden',
                'focus-within:ring-2 focus-within:ring-ring focus-within:border-transparent',
                displayError ? 'border-destructive' : 'border-input',
                transitions.colors
              )}
            >
              <input
                ref={inputRef}
                type={inputType}
                value={value}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                placeholder={placeholder}
                maxLength={maxLength}
                minLength={minLength}
                pattern={pattern}
                disabled={isSubmitting}
                className={cn(
                  'flex-1 px-3 py-2 text-sm bg-transparent',
                  'placeholder:text-muted-foreground/60',
                  'focus:outline-none',
                  'disabled:cursor-not-allowed disabled:opacity-50'
                )}
              />

              <button
                type="button"
                onClick={handleSubmit}
                disabled={isSubmitting || !value.trim()}
                className={cn(
                  'p-2 mr-1 rounded-md shrink-0',
                  'disabled:opacity-50 disabled:cursor-not-allowed',
                  value.trim()
                    ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                    : 'text-muted-foreground',
                  animations.press,
                  transitions.colors
                )}
              >
                <PaperPlaneTilt
                  className={cn('h-4 w-4', isSubmitting && 'animate-pulse')}
                  weight="fill"
                />
              </button>
            </div>

            {/* Error message */}
            {displayError && (
              <p className="text-xs text-destructive px-1">{displayError}</p>
            )}

            {/* Hints */}
            <div className="flex items-center justify-between px-1 text-xs text-muted-foreground">
              <span>
                Press <kbd className="px-1 py-0.5 rounded bg-muted font-mono">Enter</kbd> to submit
              </span>
              {maxLength && (
                <span>
                  {value.length}/{maxLength}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Answered state */}
        {status === 'answered' && (
          <div className="mt-2 px-3 py-2 rounded-lg bg-muted/50 text-sm">
            {value || initialValue}
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { QuestionPromptProps as QuestionPromptComponentProps };
