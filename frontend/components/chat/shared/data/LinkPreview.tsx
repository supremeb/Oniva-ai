'use client';

import { memo, useState } from 'react';
import { cn } from '@/lib/utils';
import { Link, Globe, ArrowSquareOut, Copy, Check } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface LinkPreviewProps {
  /** URL to preview */
  url: string;
  /** Link title */
  title?: string;
  /** Link description */
  description?: string;
  /** Preview image URL */
  imageUrl?: string;
  /** Site favicon URL */
  faviconUrl?: string;
  /** Site name */
  siteName?: string;
  /** Compact display */
  compact?: boolean;
  /** Show copy button */
  copyable?: boolean;
  /** Open in new tab */
  openInNewTab?: boolean;
  /** Callback when clicked */
  onClick?: () => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * LinkPreview - Rich link preview card
 *
 * Features:
 * - OpenGraph-style preview
 * - Image preview
 * - Copy URL button
 * - Compact mode
 */
export const LinkPreview = memo(function LinkPreview({
  url,
  title,
  description,
  imageUrl,
  faviconUrl,
  siteName,
  compact = false,
  copyable = true,
  openInNewTab = true,
  onClick,
  className,
}: LinkPreviewProps) {
  const [copied, setCopied] = useState(false);

  // Extract domain from URL
  const getDomain = (urlStr: string) => {
    try {
      return new URL(urlStr).hostname.replace('www.', '');
    } catch {
      return urlStr;
    }
  };

  const handleClick = () => {
    if (onClick) {
      onClick();
    } else if (openInNewTab) {
      window.open(url, '_blank', 'noopener,noreferrer');
    } else {
      window.location.href = url;
    }
  };

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const domain = getDomain(url);
  const displayTitle = title || domain;

  if (compact) {
    return (
      <a
        href={url}
        target={openInNewTab ? '_blank' : '_self'}
        rel={openInNewTab ? 'noopener noreferrer' : undefined}
        onClick={(e) => {
          if (onClick) {
            e.preventDefault();
            onClick();
          }
        }}
        className={cn(
          'inline-flex items-center gap-2 px-2 py-1 rounded-md border border-border',
          'text-sm hover:bg-muted/50',
          animations.press,
          transitions.colors,
          className
        )}
      >
        {faviconUrl ? (
          <img src={faviconUrl} alt="" className="w-4 h-4 rounded" />
        ) : (
          <Globe className="h-4 w-4 text-muted-foreground" />
        )}
        <span className="truncate max-w-[200px] text-foreground">{displayTitle}</span>
        <ArrowSquareOut className="h-3 w-3 text-muted-foreground shrink-0" />
      </a>
    );
  }

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        size="sm"
        className={cn('cursor-pointer overflow-hidden', className)}
        onClick={handleClick}
      >
        <div className="flex gap-3">
          {/* Image preview */}
          {imageUrl && (
            <div className="w-24 h-24 shrink-0 rounded-md overflow-hidden bg-muted">
              <img
                src={imageUrl}
                alt=""
                className="w-full h-full object-cover"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
            </div>
          )}

          {/* Content */}
          <div className="flex-1 min-w-0 py-1">
            {/* Site info */}
            <div className="flex items-center gap-2 mb-1">
              {faviconUrl ? (
                <img src={faviconUrl} alt="" className="w-4 h-4 rounded" />
              ) : (
                <Globe className="h-4 w-4 text-muted-foreground" />
              )}
              <span className="text-xs text-muted-foreground truncate">
                {siteName || domain}
              </span>
            </div>

            {/* Title */}
            <h4 className="font-medium text-sm line-clamp-2 hover:text-primary transition-colors">
              {displayTitle}
            </h4>

            {/* Description */}
            {description && (
              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                {description}
              </p>
            )}

            {/* URL */}
            <div className="flex items-center gap-2 mt-2">
              <Link className="h-3 w-3 text-muted-foreground shrink-0" />
              <span className="text-xs text-muted-foreground truncate flex-1">
                {url}
              </span>
            </div>
          </div>

          {/* Actions */}
          <div className="flex flex-col items-center gap-1 py-1">
            {copyable && (
              <button
                onClick={handleCopy}
                className={cn(
                  'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                  animations.press,
                  transitions.colors
                )}
                title="Copy URL"
              >
                {copied ? (
                  <Check className="h-4 w-4 text-green-500" weight="bold" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                window.open(url, '_blank', 'noopener,noreferrer');
              }}
              className={cn(
                'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                animations.press,
                transitions.colors
              )}
              title="Open in new tab"
            >
              <ArrowSquareOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </SpringCard>
    </FadeSlide>
  );
});

export type { LinkPreviewProps as LinkPreviewComponentProps };
