'use client';

import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { FloppyDisk, Trash, Warning } from '@phosphor-icons/react';

interface UnsavedChangesDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onDiscard: () => void;
    onSaveDraft: () => void;
    isSaving?: boolean;
}

/**
 * UnsavedChangesDialog - Prompts user to save or discard changes before navigating away
 */
export function UnsavedChangesDialog({
    open,
    onOpenChange,
    onDiscard,
    onSaveDraft,
    isSaving = false,
}: UnsavedChangesDialogProps) {
    return (
        <AlertDialog open={open} onOpenChange={onOpenChange}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle className="flex items-center gap-2">
                        <Warning className="h-5 w-5 text-amber-500" weight="fill" />
                        Unsaved Changes
                    </AlertDialogTitle>
                    <AlertDialogDescription>
                        You have unsaved changes in this workflow. Would you like to save them as a draft before leaving?
                    </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter className="gap-2 sm:gap-0">
                    <AlertDialogCancel asChild>
                        <Button variant="ghost" onClick={() => onOpenChange(false)}>
                            Cancel
                        </Button>
                    </AlertDialogCancel>
                    <Button
                        variant="destructive"
                        onClick={onDiscard}
                        className="gap-1.5"
                    >
                        <Trash className="h-4 w-4" />
                        Discard Changes
                    </Button>
                    <Button
                        onClick={onSaveDraft}
                        disabled={isSaving}
                        className="gap-1.5"
                    >
                        <FloppyDisk className="h-4 w-4" />
                        {isSaving ? 'Saving...' : 'Save Draft'}
                    </Button>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );
}
