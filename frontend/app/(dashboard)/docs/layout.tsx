import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import type { ReactNode } from 'react';
import { source } from '@/lib/source';
import { Book } from '@phosphor-icons/react/dist/ssr';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <DocsLayout
      tree={source.pageTree}
      nav={{
        title: (
          <div className="flex items-center gap-2">
            <Book className="h-5 w-5" weight="duotone" />
            <span>Documentation</span>
          </div>
        ),
        enabled: false, // Disable top nav since we have dashboard nav
      }}
      sidebar={{
        defaultOpenLevel: 1,
        banner: (
          <div className="p-3 border-b bg-fd-muted/50 text-sm text-fd-muted-foreground">
            Oniva Documentation
          </div>
        ),
      }}
    >
      {children}
    </DocsLayout>
  );
}
