'use client';

import { memo, useState, useCallback } from 'react';
import { Check, PencilSimple, X, ListChecks, File, CaretDown } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

const DELIVERABLE_OPTIONS = [
  { value: null, label: 'Markdown report only' },
  { value: 'pdf_report', label: 'PDF Report' },
  { value: 'presentation', label: 'Presentation' },
  { value: 'spreadsheet', label: 'Spreadsheet' },
  { value: 'dashboard', label: 'Dashboard' },
  { value: 'chart', label: 'Chart' },
  { value: 'document', label: 'Document' },
  { value: 'data_export', label: 'Data Export' },
] as const;

interface PlanCardProps {
  title: string;
  steps: string[];
  status: 'pending' | 'approved' | 'rejected' | 'editing';
  deliverable?: string | null;
  deliverableDescription?: string | null;
  onApprove?: () => void;
  onReject?: () => void;
  onEdit?: () => void;
  onDeliverableChange?: (type: string | null, description?: string) => void;
}

export const PlanCard = memo(function PlanCard({
  title,
  steps,
  status,
  deliverable,
  deliverableDescription,
  onApprove,
  onReject,
  onEdit,
  onDeliverableChange,
}: PlanCardProps) {
  const isPending = status === 'pending';
  const [showDropdown, setShowDropdown] = useState(false);
  const toggleDropdown = useCallback(() => setShowDropdown((v) => !v), []);

  const currentLabel = DELIVERABLE_OPTIONS.find((o) => o.value === deliverable)?.label
    || deliverable
    || 'Markdown report only';

  const handleSelect = useCallback((value: string | null) => {
    onDeliverableChange?.(value);
    setShowDropdown(false);
  }, [onDeliverableChange]);

  return (
    <div
      className={cn(
        'rounded-xl border bg-white overflow-hidden',
        isPending
          ? 'border-blue-200 shadow-sm'
          : status === 'approved'
            ? 'border-green-200'
            : 'border-neutral-200'
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-neutral-100 bg-neutral-50/50">
        <ListChecks className="h-4 w-4 text-blue-600" weight="bold" />
        <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
        {status === 'approved' && (
          <span className="ml-auto text-xs text-green-600 font-medium">Approved</span>
        )}
      </div>

      {/* Steps */}
      <div className="px-4 py-3 space-y-2">
        {steps.map((step, i) => (
          <div key={i} className="flex items-start gap-2.5 text-sm">
            <span className="flex-shrink-0 w-5 h-5 rounded-full bg-neutral-100 text-neutral-500 flex items-center justify-center text-xs font-medium mt-0.5">
              {i + 1}
            </span>
            <span className="text-neutral-700 leading-relaxed">{step}</span>
          </div>
        ))}
      </div>

      {/* Deliverable row */}
      {isPending && (
        <div className="px-4 py-2.5 border-t border-neutral-100 bg-neutral-50/20">
          <div className="flex items-center gap-2">
            <File className="h-3.5 w-3.5 text-neutral-400 flex-shrink-0" />
            <span className="text-xs text-neutral-500">Deliverable:</span>
            <div className="relative">
              <button
                onClick={toggleDropdown}
                className={cn(
                  'flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium transition-colors',
                  deliverable
                    ? 'bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100'
                    : 'bg-neutral-100 text-neutral-600 border border-neutral-200 hover:bg-neutral-150',
                )}
              >
                {currentLabel}
                <CaretDown className="h-3 w-3" />
              </button>
              {showDropdown && (
                <div className="absolute top-full left-0 mt-1 z-10 bg-white border border-neutral-200 rounded-lg shadow-lg py-1 min-w-[180px]">
                  {DELIVERABLE_OPTIONS.map((opt) => (
                    <button
                      key={String(opt.value)}
                      onClick={() => handleSelect(opt.value)}
                      className={cn(
                        'w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-50 transition-colors',
                        deliverable === opt.value
                          ? 'text-blue-700 font-medium bg-blue-50/50'
                          : 'text-neutral-700',
                      )}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          {deliverable && deliverableDescription && (
            <p className="text-xs text-neutral-400 mt-1 ml-5.5">{deliverableDescription}</p>
          )}
        </div>
      )}

      {/* Actions */}
      {isPending && (
        <div className="flex items-center gap-2 px-4 py-3 border-t border-neutral-100 bg-neutral-50/30">
          <button
            onClick={onApprove}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-medium hover:bg-blue-700 transition-colors"
          >
            <Check className="h-3.5 w-3.5" weight="bold" />
            Proceed
          </button>
          <button
            onClick={onEdit}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-neutral-200 text-neutral-600 text-xs font-medium hover:bg-neutral-50 transition-colors"
          >
            <PencilSimple className="h-3.5 w-3.5" />
            Edit
          </button>
          <button
            onClick={onReject}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-neutral-400 text-xs font-medium hover:text-neutral-600 hover:bg-neutral-50 transition-colors"
          >
            <X className="h-3.5 w-3.5" />
            Just Answer
          </button>
        </div>
      )}
    </div>
  );
});
