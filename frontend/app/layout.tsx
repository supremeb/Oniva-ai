import type { Metadata } from 'next';
import { DM_Sans } from 'next/font/google';
import { GeistMono } from 'geist/font/mono';

const dmSans = DM_Sans({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600', '700'],
  variable: '--font-sans',
  display: 'swap',
  preload: true,
});
import './globals.css';
import { QueryProvider } from '@/components/providers/QueryProvider';
import { StorageMigrationProvider } from '@/components/providers/StorageMigrationProvider';
import { Toaster } from '@/components/ui/toaster';
// import { AgentationProvider } from '@/components/providers/AgentationProvider';
import { RootProvider } from 'fumadocs-ui/provider';

export const metadata: Metadata = {
  title: 'Oniva - AI Agent Workflow Platform',
  description: 'Build, visualize, and execute composable AI agent workflows',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning className={`${dmSans.variable} ${GeistMono.variable} light`}>
      <body className="font-sans antialiased">
        <RootProvider theme={{ enabled: false }}>
          <StorageMigrationProvider>
            <QueryProvider>
              {children}
              <Toaster />
              {/* <AgentationProvider /> */}
            </QueryProvider>
          </StorageMigrationProvider>
        </RootProvider>
      </body>
    </html>
  );
}
