'use client';

import { memo, forwardRef, type KeyboardEvent } from 'react';
import { cn } from '@/lib/utils';
import { Check, Circle, RadioButton } from '@phosphor-icons/react';
import { animations, transitions } from '@/lib/animation-utils';

export interface QuestionOptionProps {
  /** Option identifier */
  id: string;
  /** Display label */
  label: string;
  /** Optional description */
  description?: string;
  /** Whether option is selected */
  selected?: boolean;
  /** Keyboard shortcut (1-9) */
  shortcut?: number;
  /** Whether this is a multi-select option (checkbox style) */
  multiSelect?: boolean;
  /** Whether option is disabled */
  disabled?: boolean;
  /** Whether option is focused (keyboard navigation) */
  focused?: boolean;
  /** Callback when selected */
  onSelect?: () => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * QuestionOption - Individual option for agent questions
 *
 * Features:
 * - Keyboard shortcut display (1-9)
 * - Radio/checkbox style based on multiSelect
 * - Focus ring for keyboard navigation
 * - Press animation
 */
export const QuestionOption = memo(
  forwardRef<HTMLButtonElement, QuestionOptionProps>(function QuestionOption(
    {
      id,
      label,
      description,
      selected = false,
      shortcut,
      multiSelect = false,
      disabled = false,
      focused = false,
      onSelect,
      className,
    },
    ref
  ) {
    const handleKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onSelect?.();
      }
    };

    return (
      <button
        ref={ref}
        type="button"
        role={multiSelect ? 'checkbox' : 'radio'}
        aria-checked={selected}
        aria-disabled={disabled}
        disabled={disabled}
        onClick={onSelect}
        onKeyDown={handleKeyDown}
        className={cn(
          'group relative w-full flex items-start gap-3 p-3 rounded-lg text-left',
          'border bg-background',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
          'disabled:cursor-not-allowed disabled:opacity-50',
          selected
            ? 'border-primary bg-primary/5'
            : 'border-border hover:border-primary/50 hover:bg-muted/50',
          focused && 'ring-2 ring-ring ring-offset-2',
          !disabled && animations.press,
          transitions.colors,
          className
        )}
      >
        {/* Selection indicator */}
        <div className="shrink-0 mt-0.5">
          {multiSelect ? (
            <div
              className={cn(
                'w-4 h-4 rounded border flex items-center justify-center',
                transitions.colors,
                selected
                  ? 'bg-primary border-primary text-primary-foreground'
                  : 'border-muted-foreground/30 group-hover:border-primary/50'
              )}
            >
              {selected && <Check className="h-3 w-3" weight="bold" />}
            </div>
          ) : (
            <div
              className={cn(
                'w-4 h-4 rounded-full border flex items-center justify-center',
                transitions.colors,
                selected
                  ? 'border-primary'
                  : 'border-muted-foreground/30 group-hover:border-primary/50'
              )}
            >
              {selected ? (
                <RadioButton className="h-4 w-4 text-primary" weight="fill" />
              ) : (
                <Circle className="h-4 w-4 text-transparent" />
              )}
            </div>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'font-medium text-sm',
                selected ? 'text-foreground' : 'text-foreground/80'
              )}
            >
              {label}
            </span>
          </div>

          {description && (
            <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
              {description}
            </p>
          )}
        </div>

        {/* Keyboard shortcut */}
        {shortcut && shortcut <= 9 && (
          <div
            className={cn(
              'shrink-0 w-5 h-5 rounded flex items-center justify-center text-xs font-mono',
              'border',
              transitions.colors,
              selected
                ? 'bg-primary/10 border-primary/30 text-primary'
                : 'bg-muted border-border text-muted-foreground group-hover:border-primary/30'
            )}
          >
            {shortcut}
          </div>
        )}
      </button>
    );
  })
);

export type { QuestionOptionProps as QuestionOptionComponentProps };
