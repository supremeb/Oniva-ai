'use client';

import { useState, useEffect } from 'react';
import { X, ListDashes } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

interface SetInstructionsModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSave: (instructions: string) => void;
    initialInstructions?: string;
}

export function SetInstructionsModal({
    isOpen,
    onClose,
    onSave,
    initialInstructions = '',
}: SetInstructionsModalProps) {
    const [instructions, setInstructions] = useState(initialInstructions);

    useEffect(() => {
        setInstructions(initialInstructions);
    }, [initialInstructions, isOpen]);

    const handleSave = () => {
        onSave(instructions);
        onClose();
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            {/* Backdrop */}
            <div
                className="absolute inset-0 bg-black/50"
                onClick={onClose}
            />

            {/* Modal */}
            <div className="relative bg-background rounded-xl shadow-lg w-full max-w-lg mx-4 overflow-hidden">
                {/* Header */}
                <div className="flex items-start gap-3 p-6 pb-4">
                    <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
                        <ListDashes className="h-5 w-5 text-foreground" />
                    </div>
                    <div className="flex-1">
                        <h2 className="text-lg font-semibold">Set Project Instructions</h2>
                        <p className="text-sm text-muted-foreground mt-0.5">
                            Instruct specific behaviors, focus, tones, or formats for the response in this project.
                        </p>
                    </div>
                    <button
                        onClick={onClose}
                        className="h-8 w-8 rounded-md hover:bg-muted flex items-center justify-center transition-colors"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                {/* Content */}
                <div className="px-6 pb-4">
                    <textarea
                        value={instructions}
                        onChange={(e) => setInstructions(e.target.value)}
                        placeholder="Think step by step and show reasoning for complex problems. Use specific examples."
                        className={cn(
                            'w-full h-32 px-3 py-2.5 rounded-lg border border-border bg-background',
                            'text-sm resize-none',
                            'focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary',
                            'placeholder:text-muted-foreground'
                        )}
                    />
                </div>

                {/* Footer */}
                <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-border">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 rounded-lg border border-border hover:bg-muted transition-colors text-sm font-medium"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSave}
                        className="px-4 py-2 rounded-lg bg-foreground text-background hover:bg-foreground/90 transition-colors text-sm font-medium"
                    >
                        Save Instructions
                    </button>
                </div>
            </div>
        </div>
    );
}
