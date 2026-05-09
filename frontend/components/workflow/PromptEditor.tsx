'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { type Node } from '@xyflow/react';
import { VariablePicker, type WorkflowVariable, VariablePill, formatVariableReference } from './VariablePicker';
import { BracketsAngle } from '@phosphor-icons/react';

interface PromptEditorProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    label?: string;
    nodes: Node[];
    currentNodeId?: string;
    minHeight?: string;
    className?: string;
}

// Parse variable references from text
function parseVariableReferences(text: string): Array<{ start: number; end: number; ref: string }> {
    const regex = /\{\{([^}]+)\}\}/g;
    const matches: Array<{ start: number; end: number; ref: string }> = [];
    let match;
    while ((match = regex.exec(text)) !== null) {
        matches.push({
            start: match.index,
            end: match.index + match[0].length,
            ref: match[1],
        });
    }
    return matches;
}

export function PromptEditor({
    value,
    onChange,
    placeholder,
    label,
    nodes,
    currentNodeId,
    minHeight = '80px',
    className = '',
}: PromptEditorProps) {
    const [isPickerOpen, setIsPickerOpen] = useState(false);
    const [pickerPosition, setPickerPosition] = useState<{ x: number; y: number } | undefined>();
    const [cursorPosition, setCursorPosition] = useState<number>(0);
    const [triggerSearch, setTriggerSearch] = useState('');
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    // Handle keyboard triggers for variable picker
    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        const textarea = e.currentTarget;
        const curPos = textarea.selectionStart;
        const textBefore = value.slice(0, curPos);

        // Check for trigger patterns
        if (e.key === '/' || e.key === '{') {
            // Check if typing {{ 
            const isDoubleBrace = e.key === '{' && textBefore.endsWith('{');
            const isSingleBrace = e.key === '{' && !textBefore.endsWith('{');
            const isSlash = e.key === '/';

            if (isDoubleBrace || isSlash || isSingleBrace) {
                // Get position for picker
                const rect = textarea.getBoundingClientRect();
                const lineHeight = 20;
                const lines = textBefore.split('\n');
                const currentLine = lines.length - 1;

                setPickerPosition({
                    x: rect.left + 20,
                    y: rect.top + (currentLine + 1) * lineHeight + 30,
                });
                setCursorPosition(curPos);
                setTriggerSearch('');

                // Open picker after a brief delay to allow the character to be typed
                setTimeout(() => setIsPickerOpen(true), 0);
            }
        }

        // Close picker on Escape
        if (e.key === 'Escape' && isPickerOpen) {
            setIsPickerOpen(false);
            e.preventDefault();
        }
    }, [value, isPickerOpen]);

    // Handle variable selection
    const handleVariableSelect = useCallback((variable: WorkflowVariable) => {
        const textarea = textareaRef.current;
        if (!textarea) return;

        const ref = formatVariableReference(variable);
        const before = value.slice(0, cursorPosition);
        const after = value.slice(cursorPosition);

        // Remove trigger characters if present
        let cleanBefore = before;
        if (before.endsWith('{{')) {
            cleanBefore = before.slice(0, -2);
        } else if (before.endsWith('{')) {
            cleanBefore = before.slice(0, -1);
        } else if (before.endsWith('/')) {
            cleanBefore = before.slice(0, -1);
        }

        const newValue = cleanBefore + ref + after;
        onChange(newValue);

        // Reset cursor position
        const newPos = cleanBefore.length + ref.length;
        setTimeout(() => {
            textarea.focus();
            textarea.setSelectionRange(newPos, newPos);
        }, 0);
    }, [value, cursorPosition, onChange]);

    // Open picker via button
    const handlePickerButtonClick = () => {
        const textarea = textareaRef.current;
        if (!textarea) return;

        const rect = textarea.getBoundingClientRect();
        setPickerPosition({
            x: rect.right - 256,
            y: rect.top + 30,
        });
        setCursorPosition(textarea.selectionStart);
        setTriggerSearch('');
        setIsPickerOpen(true);
    };

    // Highlight variable references in preview
    const renderPreview = () => {
        if (!value) return null;

        const refs = parseVariableReferences(value);
        if (refs.length === 0) return null;

        const parts: React.ReactNode[] = [];
        let lastIndex = 0;

        refs.forEach((ref, i) => {
            // Text before this reference
            if (ref.start > lastIndex) {
                parts.push(<span key={`text-${i}`}>{value.slice(lastIndex, ref.start)}</span>);
            }
            // The reference as a pill
            parts.push(
                <span key={`ref-${i}`} className="inline-flex items-center px-1 py-0.5 bg-blue-100 text-blue-700 rounded text-xs mx-0.5">
                    {ref.ref}
                </span>
            );
            lastIndex = ref.end;
        });

        // Remaining text
        if (lastIndex < value.length) {
            parts.push(<span key="text-end">{value.slice(lastIndex)}</span>);
        }

        return (
            <div className="text-xs text-muted-foreground mt-1 p-2 bg-muted/30 rounded overflow-x-auto whitespace-pre-wrap">
                {parts}
            </div>
        );
    };

    return (
        <div className={`relative ${className}`}>
            {label && (
                <div className="flex items-center justify-between mb-1">
                    <label className="text-sm font-medium">{label}</label>
                    <button
                        type="button"
                        onClick={handlePickerButtonClick}
                        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                        title="Insert variable"
                    >
                        <BracketsAngle className="h-3.5 w-3.5" weight="bold" />
                        <span>{'{x}'}</span>
                    </button>
                </div>
            )}

            <textarea
                ref={textareaRef}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={placeholder}
                className="w-full rounded-md border px-3 py-2 text-sm bg-background resize-none focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
                style={{ minHeight }}
            />

            {renderPreview()}

            <p className="text-[10px] text-muted-foreground mt-1">
                Type <code className="px-1 bg-muted rounded">/</code> or <code className="px-1 bg-muted rounded">{'{'}</code> to insert variables
            </p>

            <VariablePicker
                isOpen={isPickerOpen}
                onClose={() => setIsPickerOpen(false)}
                onSelect={handleVariableSelect}
                nodes={nodes}
                currentNodeId={currentNodeId}
                position={pickerPosition}
                searchQuery={triggerSearch}
            />
        </div>
    );
}
