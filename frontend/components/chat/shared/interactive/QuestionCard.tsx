'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { Check, Clock, Question } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { ActionButton } from './ActionButton';

export interface QuestionCardOption {
  id: string;
  label: string;
  description?: string;
}

export interface QuestionCardProps {
  id: string;
  question: string;
  options: QuestionCardOption[];
  /** More than one option may be chosen. */
  multiSelect?: boolean;
  /** A free-text answer is accepted beside (or instead of) the options. Always on without options. */
  allowFreeText?: boolean;
  status?: 'pending' | 'answered' | 'expired';
  /** The recorded answer, once answered. */
  answer?: { optionIds: string[]; text: string | null } | null;
  /** Epoch ms; a pending card past it renders as expired. */
  expiresAt?: number | null;
  /** The viewer cannot answer: no controls. */
  readOnly?: boolean;
  /**
   * A rejected promise shows its message inline and re-enables the card, unless
   * the error has `retryable === false` (the card then stays locked). On success
   * the card stays disabled until `status` changes.
   */
  onAnswer?: (answer: { optionIds: string[]; otherValue?: string }) => void | Promise<void>;
  className?: string;
}

/**
 * QuestionCard: an `ask_user` question from the engine. Same lifecycle as the
 * approval card: one send in flight at a time, inline error, locked on a
 * definitive refusal, read-only once answered or expired.
 */
export const QuestionCard = memo(function QuestionCard({
  id,
  question,
  options,
  multiSelect = false,
  allowFreeText = false,
  status: statusProp = 'pending',
  answer,
  expiresAt,
  readOnly = false,
  onAnswer,
  className,
}: QuestionCardProps) {
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const inFlight = useRef(false);

  const expired = statusProp === 'pending' && expiresAt != null && now >= expiresAt;
  const status = expired ? 'expired' : statusProp;
  useEffect(() => {
    if (statusProp !== 'pending' || expiresAt == null) return;
    const wait = expiresAt - Date.now();
    if (wait <= 0) {
      setNow(Date.now());
      return;
    }
    const t = setTimeout(() => setNow(Date.now()), wait);
    return () => clearTimeout(t);
  }, [statusProp, expiresAt]);

  const hasOptions = options.length > 0;
  const showText = allowFreeText || !hasOptions;
  const otherValue = text.trim();
  const canSend = selected.length > 0 || otherValue !== '';
  const busy = sent || locked;

  const toggle = (optionId: string) =>
    setSelected((cur) =>
      multiSelect ? (cur.includes(optionId) ? cur.filter((x) => x !== optionId) : [...cur, optionId]) : [optionId]
    );

  const send = useCallback(async () => {
    if (!onAnswer || inFlight.current || !canSend) return; // a second click while a send is in flight is ignored
    inFlight.current = true;
    setSent(true);
    setError(null);
    try {
      await onAnswer({ optionIds: selected, ...(otherValue !== '' ? { otherValue } : {}) });
    } catch (e) {
      inFlight.current = false;
      setSent(false);
      setError(e instanceof Error && e.message ? e.message : 'Could not send your answer.');
      if ((e as { retryable?: boolean } | null)?.retryable === false) setLocked(true);
    }
  }, [onAnswer, canSend, selected, otherValue]);

  const answerText = (): string => {
    if (!answer) return '';
    const labels = answer.optionIds.map((oid) => options.find((o) => o.id === oid)?.label ?? oid);
    return [...labels, ...(answer.text ? [answer.text] : [])].join(', ');
  };

  return (
    <FadeSlide>
      <SpringCard variant="outline" className={cn(status !== 'pending' && 'opacity-80', className)} data-testid="question-card" data-status={status}>
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <Question className="h-4 w-4 text-primary" weight="fill" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">{question}</p>
          </div>
        </div>

        {status === 'pending' && !readOnly && (
          <div className="mt-4 space-y-3">
            {hasOptions && (
              <fieldset className="space-y-2" disabled={busy}>
                <legend className="sr-only">{multiSelect ? 'Choose one or more' : 'Choose one'}</legend>
                {options.map((o) => (
                  <label
                    key={o.id}
                    className="flex cursor-pointer items-start gap-2 rounded-lg border border-border px-3 py-2 text-sm has-[:checked]:border-primary has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
                  >
                    <input
                      type={multiSelect ? 'checkbox' : 'radio'}
                      name={`question-${id}`}
                      className="mt-0.5"
                      checked={selected.includes(o.id)}
                      onChange={() => toggle(o.id)}
                    />
                    <span>
                      <span className="font-medium">{o.label}</span>
                      {o.description && <span className="block text-xs text-muted-foreground">{o.description}</span>}
                    </span>
                  </label>
                ))}
              </fieldset>
            )}
            {showText && (
              <input
                type="text"
                aria-label={hasOptions ? 'Something else' : 'Your answer'}
                placeholder={hasOptions ? 'Something else...' : 'Type your answer...'}
                value={text}
                disabled={busy}
                onChange={(e) => setText(e.target.value)}
                className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
              />
            )}
            {error && (
              <p role="alert" className="text-sm text-red-500">
                {error}
              </p>
            )}
            <ActionButton
              variant="primary"
              size="sm"
              onClick={send}
              disabled={!canSend || busy}
              loading={sent && !locked}
              icon={!sent ? <Check className="h-3.5 w-3.5" weight="bold" /> : undefined}
            >
              Send answer
            </ActionButton>
          </div>
        )}

        {status === 'pending' && readOnly && (
          <p className="mt-4 border-t border-border pt-4 text-xs text-muted-foreground">
            Waiting for the conversation owner or an admin to answer.
          </p>
        )}

        {status === 'answered' && (
          <div className="mt-3 flex items-center gap-2 border-t border-border pt-3 text-sm">
            <Check className="h-4 w-4 text-green-500" weight="bold" />
            <span data-testid="question-answer">{answerText()}</span>
          </div>
        )}

        {status === 'expired' && (
          <div className="mt-3 flex items-center gap-2 border-t border-border pt-3 text-sm text-muted-foreground">
            <Clock className="h-4 w-4" />
            <span>Expired, not answered</span>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});
