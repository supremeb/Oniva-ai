'use client';

import { memo, useState, useCallback, useRef, useMemo } from 'react';
import DOMPurify from 'dompurify';
import { cn } from '@/lib/utils';
import {
  ChartBar,
  ChartLine,
  ChartPie,
  ChartScatter,
  ArrowsOutSimple,
  X,
  DownloadSimple,
  CaretDown,
  Image as ImageIcon,
  FileCode,
  Table,
} from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';
import type { InteractiveChartHandle } from './InteractiveChart';

export type ChartType = 'bar' | 'line' | 'pie' | 'scatter' | 'area';

export interface ChartPreviewProps {
  /** Chart type */
  type: ChartType;
  /** Chart title */
  title?: string;
  /** Chart description */
  description?: string;
  /** Chart image URL or base64 */
  imageUrl?: string;
  /** SVG content */
  svgContent?: string;
  /** Interactive chart component (for embedded charts) */
  children?: React.ReactNode;
  /** Ref to interactive chart for export */
  chartRef?: React.RefObject<InteractiveChartHandle | null>;
  /** Enable fullscreen */
  expandable?: boolean;
  /** Enable download */
  downloadable?: boolean;
  /** Filename for download */
  downloadFilename?: string;
  /** Aspect ratio — ignored when children (interactive chart) provided */
  aspectRatio?: '16:9' | '4:3' | '1:1' | '2:1';
  /** Additional CSS classes */
  className?: string;
}

const chartIcons = {
  bar: ChartBar,
  line: ChartLine,
  pie: ChartPie,
  scatter: ChartScatter,
  area: ChartLine,
};

const aspectRatios = {
  '16:9': 'aspect-video',
  '4:3': 'aspect-4/3',
  '1:1': 'aspect-square',
  '2:1': 'aspect-[2/1]',
};

/** Validate that a URL is safe (http(s) or data:image/* only) */
function isSafeUrl(url: string): boolean {
  const trimmed = url.trim();
  return /^https?:\/\//i.test(trimmed) || /^data:image\//i.test(trimmed);
}

/**
 * ChartPreview - Display charts and visualizations
 *
 * Features:
 * - Multiple chart types
 * - Fullscreen view
 * - Download option
 * - SVG or image support
 */
export const ChartPreview = memo(function ChartPreview({
  type,
  title,
  description,
  imageUrl,
  svgContent,
  children,
  chartRef,
  expandable = true,
  downloadable = true,
  downloadFilename = 'chart.png',
  aspectRatio = '16:9',
  className,
}: ChartPreviewProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const exportMenuRef = useRef<HTMLDivElement>(null);

  const ChartIcon = chartIcons[type] || ChartBar;
  const hasInteractiveChart = !!children && !!chartRef;

  const sanitizedSvg = useMemo(
    () =>
      svgContent
        ? DOMPurify.sanitize(svgContent, {
            USE_PROFILES: { svg: true, svgFilters: true },
          })
        : '',
    [svgContent],
  );

  const safeImageUrl = useMemo(
    () => (imageUrl && isSafeUrl(imageUrl) ? imageUrl : null),
    [imageUrl],
  );

  const handleDownload = useCallback(async () => {
    if (safeImageUrl) {
      const link = document.createElement('a');
      link.href = safeImageUrl;
      link.download = downloadFilename;
      link.click();
    } else if (sanitizedSvg) {
      const blob = new Blob([sanitizedSvg], { type: 'image/svg+xml' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = downloadFilename.replace(/\.\w+$/, '.svg');
      link.click();
      URL.revokeObjectURL(url);
    }
  }, [safeImageUrl, sanitizedSvg, downloadFilename]);

  const handleExportPNG = useCallback(() => {
    chartRef?.current?.exportPNG();
    setExportMenuOpen(false);
  }, [chartRef]);

  const handleExportSVG = useCallback(() => {
    chartRef?.current?.exportSVG();
    setExportMenuOpen(false);
  }, [chartRef]);

  const handleExportCSV = useCallback(() => {
    chartRef?.current?.exportCSV();
    setExportMenuOpen(false);
  }, [chartRef]);

  const renderChart = () => {
    if (children) return children;

    if (sanitizedSvg) {
      return (
        <div
          className="w-full h-full"
          dangerouslySetInnerHTML={{ __html: sanitizedSvg }}
        />
      );
    }

    if (safeImageUrl) {
      return (
        <img
          src={safeImageUrl}
          alt={title || 'Chart'}
          className="w-full h-full object-contain"
        />
      );
    }

    // Placeholder
    return (
      <div className="w-full h-full flex items-center justify-center bg-muted/30">
        <ChartIcon className="h-12 w-12 text-muted-foreground/30" />
      </div>
    );
  };

  return (
    <>
      <FadeSlide>
        <SpringCard variant="outline" className={className}>
          {/* Header */}
          {(title || description || expandable || downloadable) && (
            <div className="flex items-start justify-between gap-3 mb-3">
              <div className="flex items-start gap-2">
                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 shrink-0">
                  <ChartIcon className="h-4 w-4 text-primary" weight="fill" />
                </div>
                <div>
                  {title && (
                    <h4 className="font-semibold text-sm">{title}</h4>
                  )}
                  {description && (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {description}
                    </p>
                  )}
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1">
                {downloadable && hasInteractiveChart && (
                  <div className="relative" ref={exportMenuRef}>
                    <button
                      onClick={() => setExportMenuOpen((prev) => !prev)}
                      className={cn(
                        'flex items-center gap-0.5 p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                        animations.press,
                        transitions.colors,
                      )}
                      title="Export chart"
                    >
                      <DownloadSimple className="h-4 w-4" />
                      <CaretDown className="h-3 w-3" />
                    </button>
                    {exportMenuOpen && (
                      <div className="absolute right-0 top-full mt-1 z-10 w-36 rounded-lg border border-border bg-background shadow-lg py-1">
                        <button
                          onClick={handleExportPNG}
                          className="flex items-center gap-2 w-full px-3 py-1.5 text-sm text-left hover:bg-muted transition-colors"
                        >
                          <ImageIcon className="h-3.5 w-3.5" />
                          PNG image
                        </button>
                        <button
                          onClick={handleExportSVG}
                          className="flex items-center gap-2 w-full px-3 py-1.5 text-sm text-left hover:bg-muted transition-colors"
                        >
                          <FileCode className="h-3.5 w-3.5" />
                          SVG vector
                        </button>
                        <button
                          onClick={handleExportCSV}
                          className="flex items-center gap-2 w-full px-3 py-1.5 text-sm text-left hover:bg-muted transition-colors"
                        >
                          <Table className="h-3.5 w-3.5" />
                          CSV data
                        </button>
                      </div>
                    )}
                  </div>
                )}
                {downloadable && !hasInteractiveChart && (safeImageUrl || sanitizedSvg) && (
                  <button
                    onClick={handleDownload}
                    className={cn(
                      'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                      animations.press,
                      transitions.colors
                    )}
                    title="Download chart"
                  >
                    <DownloadSimple className="h-4 w-4" />
                  </button>
                )}
                {expandable && (
                  <button
                    onClick={() => setIsExpanded(true)}
                    className={cn(
                      'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                      animations.press,
                      transitions.colors
                    )}
                    title="Expand"
                  >
                    <ArrowsOutSimple className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Chart */}
          <div
            className={cn(
              'rounded-lg overflow-hidden border border-border bg-background',
              !children && aspectRatios[aspectRatio],
              children && 'p-3',
            )}
          >
            {renderChart()}
          </div>
        </SpringCard>
      </FadeSlide>

      {/* Fullscreen modal */}
      {isExpanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm animate-fade-in"
          onClick={() => setIsExpanded(false)}
        >
          <div
            className="relative max-w-[90vw] max-h-[90vh] bg-background rounded-lg overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Close button */}
            <button
              onClick={() => setIsExpanded(false)}
              className={cn(
                'absolute top-2 right-2 z-10 p-2 rounded-full bg-background/80 hover:bg-background',
                'text-muted-foreground hover:text-foreground',
                animations.press,
                transitions.colors
              )}
            >
              <X className="h-5 w-5" weight="bold" />
            </button>

            {/* Chart content */}
            <div className="p-4 max-w-[85vw] max-h-[85vh] overflow-auto">
              {title && (
                <h3 className="font-semibold text-lg mb-2">{title}</h3>
              )}
              <div className="min-w-[600px]">{renderChart()}</div>
            </div>
          </div>
        </div>
      )}
    </>
  );
});

export type { ChartPreviewProps as ChartPreviewComponentProps };
