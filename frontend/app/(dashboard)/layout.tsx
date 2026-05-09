'use client';

import { Sidebar } from '@/components/layout/Sidebar';
import { AuthProvider } from '@/components/providers/AuthProvider';
// Import React Flow CSS globally to ensure it's loaded before any lazy-loaded components
import '@xyflow/react/dist/style.css';

export default function DashboardLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <AuthProvider>
            <a
                href="#main-content"
                className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50 focus:px-4 focus:py-2 focus:bg-background focus:text-foreground focus:border focus:border-border focus:rounded-lg focus:text-sm focus:font-medium"
            >
                Skip to main content
            </a>
            <div className="flex h-screen bg-background overflow-hidden">
                <Sidebar />
                <main id="main-content" aria-label="Main content" className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
                    <div className="flex-1 min-h-0 overflow-auto smooth-scroll">
                        {children}
                    </div>
                </main>
            </div>
        </AuthProvider>
    );
}
