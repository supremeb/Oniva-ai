'use client';

import { memo, useState } from 'react';
import { cn } from '@/lib/utils';
import { Image, ArrowsOutSimple, X, DownloadSimple, MagnifyingGlassPlus, MagnifyingGlassMinus } from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface ImagePreviewProps {
  /** Image source URL */
  src: string;
  /** Alt text */
  alt?: string;
  /** Image caption */
  caption?: string;
  /** Image width */
  width?: number | string;
  /** Image height */
  height?: number | string;
  /** Aspect ratio */
  aspectRatio?: '16:9' | '4:3' | '1:1' | 'auto';
  /** Enable fullscreen view */
  expandable?: boolean;
  /** Enable download */
  downloadable?: boolean;
  /** Download filename */
  downloadFilename?: string;
  /** Enable zoom controls */
  zoomable?: boolean;
  /** Border radius */
  rounded?: 'none' | 'sm' | 'md' | 'lg' | 'full';
  /** Loading state */
  loading?: boolean;
  /** Error state */
  error?: boolean;
  /** Error message */
  errorMessage?: string;
  /** Callback when clicked */
  onClick?: () => void;
  /** Additional CSS classes */
  className?: string;
}

const aspectRatios = {
  '16:9': 'aspect-video',
  '4:3': 'aspect-4/3',
  '1:1': 'aspect-square',
  'auto': '',
};

const roundedStyles = {
  none: 'rounded-none',
  sm: 'rounded',
  md: 'rounded-md',
  lg: 'rounded-lg',
  full: 'rounded-full',
};

/**
 * ImagePreview - Display images with zoom and fullscreen
 *
 * Features:
 * - Aspect ratio control
 * - Fullscreen modal
 * - Zoom controls
 * - Download option
 * - Loading/error states
 */
export const ImagePreview = memo(function ImagePreview({
  src,
  alt = 'Image',
  caption,
  width,
  height,
  aspectRatio = 'auto',
  expandable = true,
  downloadable = true,
  downloadFilename,
  zoomable = true,
  rounded = 'md',
  loading = false,
  error = false,
  errorMessage = 'Failed to load image',
  onClick,
  className,
}: ImagePreviewProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isLoading, setIsLoading] = useState(loading);
  const [hasError, setHasError] = useState(error);
  const [zoom, setZoom] = useState(1);

  const handleDownload = () => {
    const link = document.createElement('a');
    link.href = src;
    link.download = downloadFilename || src.split('/').pop() || 'image';
    link.click();
  };

  const handleZoomIn = () => setZoom((prev) => Math.min(prev + 0.25, 3));
  const handleZoomOut = () => setZoom((prev) => Math.max(prev - 0.25, 0.5));
  const handleResetZoom = () => setZoom(1);

  if (hasError) {
    return (
      <FadeSlide>
        <div
          className={cn(
            'flex flex-col items-center justify-center gap-2 p-6 border border-dashed border-border',
            roundedStyles[rounded],
            aspectRatios[aspectRatio],
            className
          )}
        >
          <Image className="h-8 w-8 text-muted-foreground/50" weight="fill" />
          <p className="text-sm text-muted-foreground">{errorMessage}</p>
        </div>
      </FadeSlide>
    );
  }

  return (
    <>
      <FadeSlide>
        <figure className={cn('relative group', className)}>
          {/* Image container */}
          <div
            className={cn(
              'relative overflow-hidden border border-border bg-muted/30',
              roundedStyles[rounded],
              aspectRatios[aspectRatio],
              (expandable || onClick) && 'cursor-pointer'
            )}
            style={{ width, height }}
            onClick={() => {
              if (expandable) setIsExpanded(true);
              onClick?.();
            }}
          >
            {/* Loading state */}
            {isLoading && (
              <div className="absolute inset-0 flex items-center justify-center bg-muted animate-pulse">
                <Image className="h-8 w-8 text-muted-foreground/30" />
              </div>
            )}

            {/* Image */}
            <img
              src={src}
              alt={alt}
              className={cn(
                'w-full h-full object-contain',
                isLoading && 'invisible'
              )}
              onLoad={() => setIsLoading(false)}
              onError={() => {
                setIsLoading(false);
                setHasError(true);
              }}
            />

            {/* Hover overlay */}
            {(expandable || downloadable) && (
              <div
                className={cn(
                  'absolute inset-0 flex items-center justify-center gap-2',
                  'bg-black/50 opacity-0 group-hover:opacity-100',
                  transitions.colors
                )}
              >
                {expandable && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setIsExpanded(true);
                    }}
                    className={cn(
                      'p-2 rounded-full bg-white/20 text-white hover:bg-white/30',
                      animations.press,
                      transitions.colors
                    )}
                    title="Expand"
                  >
                    <ArrowsOutSimple className="h-5 w-5" weight="bold" />
                  </button>
                )}
                {downloadable && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDownload();
                    }}
                    className={cn(
                      'p-2 rounded-full bg-white/20 text-white hover:bg-white/30',
                      animations.press,
                      transitions.colors
                    )}
                    title="Download"
                  >
                    <DownloadSimple className="h-5 w-5" weight="bold" />
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Caption */}
          {caption && (
            <figcaption className="mt-2 text-xs text-muted-foreground text-center">
              {caption}
            </figcaption>
          )}
        </figure>
      </FadeSlide>

      {/* Fullscreen modal */}
      {isExpanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 animate-fade-in"
          onClick={() => setIsExpanded(false)}
        >
          {/* Controls */}
          <div className="absolute top-4 right-4 flex items-center gap-2 z-10">
            {zoomable && (
              <>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleZoomOut();
                  }}
                  className={cn(
                    'p-2 rounded-full bg-white/10 text-white hover:bg-white/20',
                    animations.press,
                    transitions.colors
                  )}
                  title="Zoom out"
                >
                  <MagnifyingGlassMinus className="h-5 w-5" />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleResetZoom();
                  }}
                  className={cn(
                    'px-2 py-1 rounded-full bg-white/10 text-white text-sm hover:bg-white/20',
                    animations.press,
                    transitions.colors
                  )}
                >
                  {Math.round(zoom * 100)}%
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleZoomIn();
                  }}
                  className={cn(
                    'p-2 rounded-full bg-white/10 text-white hover:bg-white/20',
                    animations.press,
                    transitions.colors
                  )}
                  title="Zoom in"
                >
                  <MagnifyingGlassPlus className="h-5 w-5" />
                </button>
              </>
            )}
            {downloadable && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleDownload();
                }}
                className={cn(
                  'p-2 rounded-full bg-white/10 text-white hover:bg-white/20',
                  animations.press,
                  transitions.colors
                )}
                title="Download"
              >
                <DownloadSimple className="h-5 w-5" />
              </button>
            )}
            <button
              onClick={() => setIsExpanded(false)}
              className={cn(
                'p-2 rounded-full bg-white/10 text-white hover:bg-white/20',
                animations.press,
                transitions.colors
              )}
              title="Close"
            >
              <X className="h-5 w-5" weight="bold" />
            </button>
          </div>

          {/* Image */}
          <div
            className="overflow-auto max-w-[90vw] max-h-[90vh]"
            onClick={(e) => e.stopPropagation()}
          >
            <img
              src={src}
              alt={alt}
              className="transition-transform duration-200"
              style={{ transform: `scale(${zoom})` }}
            />
          </div>

          {/* Caption */}
          {caption && (
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg bg-black/50 text-white text-sm">
              {caption}
            </div>
          )}
        </div>
      )}
    </>
  );
});

export type { ImagePreviewProps as ImagePreviewComponentProps };
