'use client';

import { useRef, useEffect, useCallback, memo } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { PaperPlaneTilt, CircleNotch, Stop, Sparkle } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

export interface ChatInputFieldProps {
  /** Current input value */
  value: string;
  /** Called when input value changes */
  onChange: (value: string) => void;
  /** Called when form is submitted */
  onSubmit: () => void;
  /** Called to stop current generation */
  onStop?: () => void;
  /** Whether a message is being sent/generated */
  isLoading?: boolean;
  /** Whether generation can be stopped */
  isStreaming?: boolean;
  /** Placeholder text */
  placeholder?: string;
  /** Disable the input */
  disabled?: boolean;
  /** Error message to display */
  error?: string | null;
  /** Additional CSS classes */
  className?: string;
  /** Compact mode for smaller chat panels */
  compact?: boolean;
  /** Maximum height for textarea (in px) */
  maxHeight?: number;
  /** Auto-focus the input on mount */
  autoFocus?: boolean;
  /** Show AI sparkle indicator */
  showAiIndicator?: boolean;
}

export const ChatInputField = memo(function ChatInputField({
  value,
  onChange,
  onSubmit,
  onStop,
  isLoading = false,
  isStreaming = false,
  placeholder = 'Type a message...',
  disabled = false,
  error,
  className,
  compact = false,
  maxHeight = 200,
  autoFocus = false,
  showAiIndicator = false,
}: ChatInputFieldProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea based on content
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'inherit';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = `${Math.min(scrollHeight, maxHeight)}px`;
    }
  }, [value, maxHeight]);

  // Auto-focus on mount if requested
  useEffect(() => {
    if (autoFocus && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [autoFocus]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (value.trim() && !isLoading && !disabled) {
          onSubmit();
        }
      }
    },
    [value, isLoading, disabled, onSubmit]
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim() && !isLoading && !disabled) {
      onSubmit();
    }
  };

  const handleStop = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onStop?.();
  };

  const canSubmit = value.trim() && !isLoading && !disabled;
  const showStopButton = isStreaming && onStop;

  return (
    <div className={cn('border-t bg-background/95 backdrop-blur-sm', className)}>
      {/* Error banner */}
      {error && (
        <div className="px-4 py-2 bg-destructive/10 border-b border-destructive/20">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className={cn(
          'flex items-end gap-2',
          compact ? 'p-2' : 'p-3'
        )}
      >
        <div
          className={cn(
            'flex-1 relative flex items-end gap-2 rounded-xl border',
            'bg-background',
            'transition-all duration-200',
            'focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary/50',
            disabled && 'opacity-60 cursor-not-allowed',
            compact ? 'px-2 py-1' : 'px-3 py-2'
          )}
        >
          {/* AI indicator */}
          {showAiIndicator && (
            <Sparkle
              className={cn(
                'shrink-0 text-violet-500',
                compact ? 'h-4 w-4' : 'h-5 w-5'
              )}
              weight="duotone"
            />
          )}

          <Textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            className={cn(
              'flex-1 resize-none bg-transparent',
              'border-0 focus-visible:ring-0 focus-visible:ring-offset-0',
              'placeholder:text-muted-foreground/60',
              compact ? 'min-h-[32px] max-h-16 text-sm py-1' : 'min-h-[40px] text-sm py-1.5'
            )}
            rows={1}
            style={{ maxHeight: `${maxHeight}px` }}
          />

          {/* Character count indicator for long messages */}
          {value.length > 500 && (
            <span className="absolute bottom-1 right-12 text-[10px] text-muted-foreground/60">
              {value.length}
            </span>
          )}
        </div>

        {/* Action button */}
        {showStopButton ? (
          <Button
            type="button"
            size="icon"
            variant="outline"
            onClick={handleStop}
            className={cn(
              'shrink-0 rounded-xl border-destructive/50 hover:bg-destructive/10',
              'transition-all duration-200',
              compact ? 'h-8 w-8' : 'h-10 w-10'
            )}
            title="Stop generating"
          >
            <Stop className={compact ? 'h-4 w-4' : 'h-5 w-5'} weight="fill" />
            <span className="sr-only">Stop</span>
          </Button>
        ) : (
          <Button
            type="submit"
            size="icon"
            disabled={!canSubmit}
            className={cn(
              'shrink-0 rounded-xl',
              'transition-all duration-200',
              'shadow-sm',
              canSubmit && 'shadow-primary/20',
              compact ? 'h-8 w-8' : 'h-10 w-10'
            )}
          >
            {isLoading ? (
              <CircleNotch className={cn('animate-spin', compact ? 'h-4 w-4' : 'h-5 w-5')} />
            ) : (
              <PaperPlaneTilt className={compact ? 'h-4 w-4' : 'h-5 w-5'} weight="fill" />
            )}
            <span className="sr-only">Send</span>
          </Button>
        )}
      </form>

      {/* Helper text */}
      <div className={cn('text-center', compact ? 'pb-1 px-2' : 'pb-2 px-3')}>
        <p className="text-[10px] text-muted-foreground/50">
          Press Enter to send, Shift + Enter for new line
        </p>
      </div>
    </div>
  );
});
