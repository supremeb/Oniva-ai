'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { AlertCircle, RefreshCw } from 'lucide-react';

export default function DashboardError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        // Log the error to error reporting service
        console.error('Dashboard Error:', error);
    }, [error]);

    return (
        <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center">
            <div className="text-center space-y-4 max-w-md">
                <AlertCircle className="h-16 w-16 text-destructive mx-auto" />
                <h1 className="text-2xl font-bold">Something went wrong!</h1>
                <p className="text-muted-foreground">
                    {error.message || 'An unexpected error occurred while loading this page.'}
                </p>
                {error.digest && (
                    <p className="text-xs text-muted-foreground">
                        Error ID: {error.digest}
                    </p>
                )}
                <div className="flex gap-3 justify-center pt-4">
                    <Button
                        onClick={() => reset()}
                        variant="default"
                        className="gap-2"
                    >
                        <RefreshCw className="h-4 w-4" />
                        Try again
                    </Button>
                    <Button
                        onClick={() => window.location.href = '/'}
                        variant="outline"
                    >
                        Go to Dashboard
                    </Button>
                </div>
            </div>
        </div>
    );
}