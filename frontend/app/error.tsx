'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { AlertTriangle, Home, RefreshCw } from 'lucide-react';

export default function GlobalError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        // Log the error to error reporting service
        console.error('Global Error:', error);
    }, [error]);

    return (
        <html>
            <body>
                <div className="flex min-h-screen items-center justify-center bg-background">
                    <div className="text-center space-y-4 max-w-lg p-8">
                        <AlertTriangle className="h-20 w-20 text-destructive mx-auto" />
                        <h1 className="text-3xl font-bold">Oops! Something went wrong</h1>
                        <p className="text-lg text-muted-foreground">
                            We encountered an unexpected error. Please try again or return to the homepage.
                        </p>
                        <details className="text-left bg-muted/50 p-4 rounded-lg">
                            <summary className="cursor-pointer font-medium">Error Details</summary>
                            <pre className="mt-2 text-sm overflow-auto">
                                {error.message || 'Unknown error'}
                                {error.stack && '\n\n' + error.stack}
                            </pre>
                        </details>
                        <div className="flex gap-3 justify-center pt-4">
                            <Button
                                onClick={() => reset()}
                                variant="default"
                                className="gap-2"
                            >
                                <RefreshCw className="h-4 w-4" />
                                Try Again
                            </Button>
                            <Button
                                onClick={() => window.location.href = '/'}
                                variant="outline"
                                className="gap-2"
                            >
                                <Home className="h-4 w-4" />
                                Go Home
                            </Button>
                        </div>
                    </div>
                </div>
            </body>
        </html>
    );
}