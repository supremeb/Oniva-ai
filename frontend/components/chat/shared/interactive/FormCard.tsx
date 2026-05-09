'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { ClipboardText, Check, Warning } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { ActionButton } from './ActionButton';
import { transitions } from '@/lib/animation-utils';

export interface FormField {
  id: string;
  label: string;
  type: 'text' | 'number' | 'email' | 'url' | 'password' | 'textarea' | 'select';
  placeholder?: string;
  required?: boolean;
  defaultValue?: string;
  options?: Array<{ value: string; label: string }>; // For select type
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  helpText?: string;
}

export interface FormCardProps {
  /** Unique identifier */
  id: string;
  /** Form title */
  title: string;
  /** Form description */
  description?: string;
  /** Form fields */
  fields: FormField[];
  /** Submit button text */
  submitText?: string;
  /** Cancel button text */
  cancelText?: string;
  /** Show cancel button */
  showCancel?: boolean;
  /** Current state */
  status?: 'pending' | 'submitted' | 'error';
  /** Error message */
  error?: string;
  /** Callback when submitted */
  onSubmit?: (values: Record<string, string>) => void;
  /** Callback when cancelled */
  onCancel?: () => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * FormCard - Multi-field form for agent data collection
 *
 * Features:
 * - Multiple field types
 * - Validation
 * - Error states
 * - Submit/Cancel actions
 */
export const FormCard = memo(function FormCard({
  id,
  title,
  description,
  fields,
  submitText = 'Submit',
  cancelText = 'Cancel',
  showCancel = false,
  status = 'pending',
  error,
  onSubmit,
  onCancel,
  className,
}: FormCardProps) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    fields.reduce((acc, field) => {
      acc[field.id] = field.defaultValue || '';
      return acc;
    }, {} as Record<string, string>)
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  const isPending = status === 'pending';

  // Handle field change
  const handleChange = useCallback((fieldId: string, value: string) => {
    setValues((prev) => ({ ...prev, [fieldId]: value }));
    setErrors((prev) => {
      const next = { ...prev };
      delete next[fieldId];
      return next;
    });
  }, []);

  // Validate form
  const validate = useCallback((): boolean => {
    const newErrors: Record<string, string> = {};

    for (const field of fields) {
      const value = values[field.id] || '';

      if (field.required && !value.trim()) {
        newErrors[field.id] = `${field.label} is required`;
        continue;
      }

      if (value && field.minLength && value.length < field.minLength) {
        newErrors[field.id] = `Minimum ${field.minLength} characters`;
        continue;
      }

      if (value && field.maxLength && value.length > field.maxLength) {
        newErrors[field.id] = `Maximum ${field.maxLength} characters`;
        continue;
      }

      if (value && field.pattern && !new RegExp(field.pattern).test(value)) {
        newErrors[field.id] = 'Invalid format';
        continue;
      }

      if (value && field.type === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
        newErrors[field.id] = 'Invalid email address';
        continue;
      }

      if (value && field.type === 'url') {
        try {
          new URL(value);
        } catch {
          newErrors[field.id] = 'Invalid URL';
        }
      }
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [fields, values]);

  // Handle submit
  const handleSubmit = useCallback(async () => {
    if (!validate() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await onSubmit?.(values);
    } finally {
      setIsSubmitting(false);
    }
  }, [validate, isSubmitting, values, onSubmit]);

  // Render field
  const renderField = (field: FormField) => {
    const fieldValue = values[field.id] || '';
    const fieldError = errors[field.id];

    const baseInputClass = cn(
      'w-full px-3 py-2 text-sm rounded-md border bg-background',
      'placeholder:text-muted-foreground/60',
      'focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent',
      'disabled:cursor-not-allowed disabled:opacity-50',
      fieldError ? 'border-destructive' : 'border-input',
      transitions.colors
    );

    return (
      <div key={field.id} className="space-y-1.5">
        <label className="text-sm font-medium text-foreground">
          {field.label}
          {field.required && <span className="text-destructive ml-1">*</span>}
        </label>

        {field.type === 'textarea' ? (
          <textarea
            value={fieldValue}
            onChange={(e) => handleChange(field.id, e.target.value)}
            placeholder={field.placeholder}
            maxLength={field.maxLength}
            disabled={!isPending || isSubmitting}
            rows={3}
            className={cn(baseInputClass, 'resize-none')}
          />
        ) : field.type === 'select' ? (
          <select
            value={fieldValue}
            onChange={(e) => handleChange(field.id, e.target.value)}
            disabled={!isPending || isSubmitting}
            className={baseInputClass}
          >
            <option value="">{field.placeholder || 'Select...'}</option>
            {field.options?.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        ) : (
          <input
            type={field.type}
            value={fieldValue}
            onChange={(e) => handleChange(field.id, e.target.value)}
            placeholder={field.placeholder}
            maxLength={field.maxLength}
            minLength={field.minLength}
            pattern={field.pattern}
            disabled={!isPending || isSubmitting}
            className={baseInputClass}
          />
        )}

        {field.helpText && !fieldError && (
          <p className="text-xs text-muted-foreground">{field.helpText}</p>
        )}

        {fieldError && (
          <p className="text-xs text-destructive flex items-center gap-1">
            <Warning className="h-3 w-3" weight="fill" />
            {fieldError}
          </p>
        )}
      </div>
    );
  };

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        className={cn(
          status === 'error' && 'border-destructive/30',
          className
        )}
      >
        {/* Header */}
        <div className="flex items-start gap-3 mb-4">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 shrink-0">
            {status === 'submitted' ? (
              <Check className="h-4 w-4 text-green-500" weight="bold" />
            ) : (
              <ClipboardText className="h-4 w-4 text-primary" weight="fill" />
            )}
          </div>
          <div className="flex-1">
            <h4 className="font-semibold text-sm">{title}</h4>
            {description && (
              <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
            )}
          </div>
        </div>

        {/* Form error */}
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-destructive/10 border border-destructive/20">
            <p className="text-sm text-destructive flex items-center gap-2">
              <Warning className="h-4 w-4" weight="fill" />
              {error}
            </p>
          </div>
        )}

        {/* Fields */}
        {isPending && (
          <div className="space-y-4">
            {fields.map(renderField)}
          </div>
        )}

        {/* Submitted state */}
        {status === 'submitted' && (
          <div className="space-y-2">
            {fields.map((field) => (
              <div key={field.id} className="flex items-center justify-between py-1.5 px-2 rounded bg-muted/50 text-sm">
                <span className="text-muted-foreground">{field.label}</span>
                <span className={cn('font-mono text-xs', field.type === 'password' && 'blur-sm')}>
                  {values[field.id] || '-'}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Actions */}
        {isPending && (
          <div className="flex items-center gap-2 mt-4 pt-4 border-t border-border">
            {showCancel && onCancel && (
              <ActionButton
                variant="outline"
                size="sm"
                onClick={onCancel}
                disabled={isSubmitting}
              >
                {cancelText}
              </ActionButton>
            )}
            <ActionButton
              variant="primary"
              size="sm"
              onClick={handleSubmit}
              loading={isSubmitting}
              icon={!isSubmitting ? <Check className="h-3.5 w-3.5" weight="bold" /> : undefined}
            >
              {submitText}
            </ActionButton>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { FormCardProps as FormCardComponentProps };
