'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  Key,
  Check,
  Warning,
  ArrowSquareOut,
  ShieldCheck,
  CircleNotch,
  X,
} from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { ActionButton } from './ActionButton';
import { transitions } from '@/lib/animation-utils';

export interface OAuthCardProps {
  /** Unique identifier */
  id: string;
  /** Service/provider name */
  serviceName: string;
  /** Service icon URL or component */
  serviceIcon?: React.ReactNode;
  /** Service description */
  description?: string;
  /** Scopes being requested */
  scopes?: string[];
  /** OAuth state */
  status?: 'idle' | 'authorizing' | 'success' | 'error' | 'expired';
  /** Error message */
  error?: string;
  /** Connected account info */
  accountInfo?: {
    email?: string;
    name?: string;
    avatar?: string;
  };
  /** OAuth URL (opens in new window) */
  authUrl?: string;
  /** Callback when authorize clicked */
  onAuthorize?: () => void;
  /** Callback when disconnect clicked */
  onDisconnect?: () => void;
  /** Callback when retry clicked */
  onRetry?: () => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * OAuthCard - OAuth authentication flow card
 *
 * Features:
 * - Service branding
 * - Scope display
 * - Authorization flow states
 * - Connected account display
 * - Disconnect option
 */
export const OAuthCard = memo(function OAuthCard({
  id,
  serviceName,
  serviceIcon,
  description,
  scopes = [],
  status = 'idle',
  error,
  accountInfo,
  authUrl,
  onAuthorize,
  onDisconnect,
  onRetry,
  className,
}: OAuthCardProps) {
  const [isOpening, setIsOpening] = useState(false);

  const handleAuthorize = useCallback(() => {
    if (authUrl) {
      setIsOpening(true);
      // Open OAuth popup
      const width = 500;
      const height = 600;
      const left = window.screenX + (window.outerWidth - width) / 2;
      const top = window.screenY + (window.outerHeight - height) / 2;

      window.open(
        authUrl,
        `oauth_${id}`,
        `width=${width},height=${height},left=${left},top=${top},popup=1`
      );

      setTimeout(() => setIsOpening(false), 1000);
    }
    onAuthorize?.();
  }, [id, authUrl, onAuthorize]);

  const isConnected = status === 'success' && accountInfo;
  const isAuthorizing = status === 'authorizing' || isOpening;

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        className={cn(
          status === 'error' && 'border-destructive/30',
          status === 'success' && 'border-green-500/30',
          className
        )}
      >
        {/* Header */}
        <div className="flex items-start gap-3">
          {/* Service icon */}
          <div
            className={cn(
              'flex items-center justify-center w-10 h-10 rounded-lg shrink-0',
              status === 'success' ? 'bg-green-500/10' : 'bg-muted'
            )}
          >
            {serviceIcon || (
              <Key
                className={cn(
                  'h-5 w-5',
                  status === 'success' ? 'text-green-500' : 'text-muted-foreground'
                )}
                weight="fill"
              />
            )}
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <h4 className="font-semibold text-sm">{serviceName}</h4>
              {status === 'success' && (
                <span className="flex items-center gap-1 text-xs text-green-500">
                  <Check className="h-3 w-3" weight="bold" />
                  Connected
                </span>
              )}
              {status === 'error' && (
                <span className="flex items-center gap-1 text-xs text-destructive">
                  <Warning className="h-3 w-3" weight="fill" />
                  Failed
                </span>
              )}
              {status === 'expired' && (
                <span className="text-xs text-orange-500">Expired</span>
              )}
            </div>

            {description && !isConnected && (
              <p className="text-xs text-muted-foreground">{description}</p>
            )}
          </div>
        </div>

        {/* Scopes */}
        {scopes.length > 0 && !isConnected && (
          <div className="mt-3 pt-3 border-t border-border">
            <div className="text-xs text-muted-foreground mb-2">
              This will request access to:
            </div>
            <ul className="space-y-1">
              {scopes.map((scope, index) => (
                <li
                  key={index}
                  className="flex items-center gap-2 text-xs text-foreground"
                >
                  <ShieldCheck className="h-3.5 w-3.5 text-primary shrink-0" />
                  <span>{scope}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Connected account info */}
        {isConnected && accountInfo && (
          <div className="mt-3 pt-3 border-t border-green-500/20">
            <div className="flex items-center gap-3">
              {accountInfo.avatar ? (
                <img
                  src={accountInfo.avatar}
                  alt=""
                  className="w-8 h-8 rounded-full"
                />
              ) : (
                <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center">
                  <span className="text-sm font-medium text-muted-foreground">
                    {accountInfo.name?.[0] || accountInfo.email?.[0] || '?'}
                  </span>
                </div>
              )}
              <div className="flex-1 min-w-0">
                {accountInfo.name && (
                  <div className="text-sm font-medium truncate">
                    {accountInfo.name}
                  </div>
                )}
                {accountInfo.email && (
                  <div className="text-xs text-muted-foreground truncate">
                    {accountInfo.email}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Error message */}
        {error && (
          <div className="mt-3 p-2 rounded-lg bg-destructive/10 border border-destructive/20">
            <p className="text-xs text-destructive">{error}</p>
          </div>
        )}

        {/* Actions */}
        <div className="mt-4 pt-3 border-t border-border flex items-center gap-2">
          {status === 'idle' && (
            <ActionButton
              variant="primary"
              size="sm"
              onClick={handleAuthorize}
              loading={isAuthorizing}
              icon={
                !isAuthorizing ? (
                  <ArrowSquareOut className="h-3.5 w-3.5" weight="bold" />
                ) : undefined
              }
            >
              Connect {serviceName}
            </ActionButton>
          )}

          {status === 'authorizing' && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CircleNotch className="h-4 w-4 animate-spin" />
              <span>Waiting for authorization...</span>
            </div>
          )}

          {status === 'success' && onDisconnect && (
            <ActionButton
              variant="outline"
              size="sm"
              onClick={onDisconnect}
              icon={<X className="h-3.5 w-3.5" weight="bold" />}
            >
              Disconnect
            </ActionButton>
          )}

          {(status === 'error' || status === 'expired') && (
            <ActionButton
              variant="primary"
              size="sm"
              onClick={onRetry || handleAuthorize}
              icon={<ArrowSquareOut className="h-3.5 w-3.5" weight="bold" />}
            >
              Try Again
            </ActionButton>
          )}
        </div>

        {/* Security note */}
        {status === 'idle' && (
          <div className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>
              You&apos;ll be redirected to {serviceName} to authorize. We never store your password.
            </span>
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

export type { OAuthCardProps as OAuthCardComponentProps };
