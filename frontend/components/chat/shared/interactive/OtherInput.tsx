'use client';

import { memo, useState, useCallback, useRef, useEffect, type KeyboardEvent } from 'react';
import { cn } from '@/lib/utils';
import { PaperPlaneTilt, X } from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface OtherInputProps {
  /** Placeholder text */
  placeholder?: string;
  /** Whether the input is expanded/visible */
  expanded?: boolean;
  /** Current value */
  value?: string;
  /** Auto-focus when expanded */
  autoFocus?: boolean;
  /** Maximum character length */
  maxLength?: number;
  /** Whether input is disabled */
  disabled?: boolean;
  /** Callback when value changes */
  onChange?: (value: string) => void;
  /** Callback when submitted (Enter key) */
  onSubmit?: (value: string) => void;
  /** Callback when cancelled (Escape key) */
  onCancel?: () => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * OtherInput - Expandable text input for "Other" option
 *
 * Features:
 * - Smooth expand animation
 * - Auto-focus on expand
 * - Character count
 * - Enter to submit, Escape to cancel
 * - Send button
 */
export const OtherInput = memo(function OtherInput({
  placeholder = 'Type your answer...',
  expanded = false,
  value = '',
  autoFocus = true,
  maxLength = 500,
  disabled = false,
  onChange,
  onSubmit,
  onCancel,
  className,
}: OtherInputProps) {
  const [localValue, setLocalValue] = useState(value);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Sync with external value
  useEffect(() => {
    setLocalValue(value);
  }, [value]);

  // Auto-focus when expanded
  useEffect(() => {
    if (expanded && autoFocus && inputRef.current) {
      // Small delay to ensure animation has started
      const timer = setTimeout(() => {
        inputRef.current?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [expanded, autoFocus]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const newValue = e.target.value;
      if (maxLength && newValue.length > maxLength) return;
      setLocalValue(newValue);
      onChange?.(newValue);
    },
    [maxLength, onChange]
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel?.();
      } else if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (localValue.trim()) {
          onSubmit?.(localValue.trim());
        }
      }
    },
    [localValue, onCancel, onSubmit]
  );

  const handleSubmit = useCallback(() => {
    if (localValue.trim() && !disabled) {
      onSubmit?.(localValue.trim());
    }
  }, [localValue, disabled, onSubmit]);

  if (!expanded) {
    return null;
  }

  return (
    <FadeSlide className={className}>
      <div
        className={cn(
          'relative rounded-lg border border-primary/30 bg-primary/5 overflow-hidden',
          'focus-within:border-primary focus-within:ring-1 focus-within:ring-primary/30',
          transitions.colors
        )}
      >
        {/* Textarea */}
        <textarea
          ref={inputRef}
          value={localValue}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={2}
          className={cn(
            'w-full resize-none bg-transparent px-3 py-2.5 pr-12 text-sm',
            'placeholder:text-muted-foreground/60',
            'focus:outline-none',
            'disabled:cursor-not-allowed disabled:opacity-50'
          )}
        />

        {/* Actions */}
        <div className="absolute bottom-2 right-2 flex items-center gap-2">
          {/* Character count */}
          {maxLength && (
            <span
              className={cn(
                'text-xs',
                localValue.length > maxLength * 0.9
                  ? 'text-orange-500'
                  : 'text-muted-foreground/50'
              )}
            >
              {localValue.length}/{maxLength}
            </span>
          )}

          {/* Cancel button */}
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              disabled={disabled}
              className={cn(
                'p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted',
                animations.press,
                transitions.colors
              )}
              title="Cancel (Esc)"
            >
              <X className="h-4 w-4" weight="bold" />
            </button>
          )}

          {/* Submit button */}
          <button
            type="button"
            onClick={handleSubmit}
            disabled={disabled || !localValue.trim()}
            className={cn(
              'p-1.5 rounded-md',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              localValue.trim()
                ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                : 'bg-muted text-muted-foreground',
              animations.press,
              transitions.colors
            )}
            title="Submit (Enter)"
          >
            <PaperPlaneTilt className="h-4 w-4" weight="fill" />
          </button>
        </div>
      </div>

      {/* Hint */}
      <div className="flex items-center justify-between mt-1.5 px-1 text-xs text-muted-foreground/60">
        <span>Press Enter to submit, Escape to cancel</span>
      </div>
    </FadeSlide>
  );
});

export type { OtherInputProps as OtherInputComponentProps };
