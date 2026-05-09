'use client';

import { Button } from '@/components/ui/button';
import { ArrowCounterClockwise, ArrowClockwise, Minus, Plus, ArrowsIn, Clock } from '@phosphor-icons/react';

interface CanvasControlsProps {
    zoom: number;
    onZoomIn: () => void;
    onZoomOut: () => void;
    onFitView: () => void;
    onUndo: () => void;
    onRedo: () => void;
    canUndo: boolean;
    canRedo: boolean;
}

export function CanvasControls({
    zoom,
    onZoomIn,
    onZoomOut,
    onFitView,
    onUndo,
    onRedo,
    canUndo,
    canRedo,
}: CanvasControlsProps) {
    const zoomPercent = Math.round(zoom * 100);

    return (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 flex items-center gap-2 px-3 py-2 bg-background/95 backdrop-blur border rounded-lg shadow-sm">
            {/* Undo/Redo */}
            <div className="flex items-center gap-1 pr-2 border-r">
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={onUndo}
                    disabled={!canUndo}
                    title="Undo"
                >
                    <ArrowCounterClockwise className="h-4 w-4" />
                </Button>
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={onRedo}
                    disabled={!canRedo}
                    title="Redo"
                >
                    <ArrowClockwise className="h-4 w-4" />
                </Button>
            </div>

            {/* Version History */}
            <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                title="Version History"
            >
                <Clock className="h-4 w-4" />
            </Button>

            {/* Separator */}
            <div className="w-px h-5 bg-border" />

            {/* Zoom Controls */}
            <div className="flex items-center gap-1">
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={onZoomOut}
                    disabled={zoomPercent <= 25}
                    title="Zoom out"
                >
                    <Minus className="h-4 w-4" />
                </Button>
                <span className="w-12 text-center text-xs font-medium text-muted-foreground">
                    {zoomPercent}%
                </span>
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={onZoomIn}
                    disabled={zoomPercent >= 200}
                    title="Zoom in"
                >
                    <Plus className="h-4 w-4" />
                </Button>
            </div>

            {/* Fit View */}
            <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={onFitView}
                title="Fit to view"
            >
                <ArrowsIn className="h-4 w-4" />
            </Button>
        </div>
    );
}
