'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, useEffect } from 'react';

const STORAGE_VERSION_KEY = 'oniva_storage_version';

export function QueryProvider({ children }: { children: React.ReactNode }) {
    const [queryClient] = useState(
        () =>
            new QueryClient({
                defaultOptions: {
                    queries: {
                        staleTime: 60 * 1000, // 1 minute
                        gcTime: 5 * 60 * 1000, // 5 minutes cache retention
                        retry: 1,
                        retryDelay: 500,
                        networkMode: 'always',
                        // refetchOnMount ensures fresh data on navigation (the real fix for stale data)
                        // refetchOnWindowFocus disabled — it causes a stampede of requests on tab switch
                        refetchOnWindowFocus: false,
                        refetchOnMount: true,
                        refetchOnReconnect: true,
                        // Structural sharing for better performance
                        structuralSharing: true,
                    },
                    mutations: {
                        retry: 1,
                        retryDelay: 500,
                        networkMode: 'always',
                    },
                },
            })
    );

    // Clear query cache when storage version changes (e.g., schema migration)
    useEffect(() => {
        const handleStorageChange = (e: StorageEvent) => {
            if (e.key === STORAGE_VERSION_KEY) {
                console.log('[QueryProvider] Storage version changed, clearing query cache');
                queryClient.clear();
            }
        };
        window.addEventListener('storage', handleStorageChange);
        return () => window.removeEventListener('storage', handleStorageChange);
    }, [queryClient]);

    return (
        <QueryClientProvider client={queryClient}>
            {children}
        </QueryClientProvider>
    );
}
