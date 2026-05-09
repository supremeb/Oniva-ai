'use client';

import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { CaretRight } from '@phosphor-icons/react';
import { LLMProviderForm } from '@/components/settings/LLMProviderForm';

export default function NewLLMProviderPage() {
  const router = useRouter();

  return (
    <div className="p-6 lg:p-8 max-w-2xl">
      <nav className="flex items-center gap-1.5 text-sm text-muted-foreground mb-6" aria-label="Breadcrumb">
        <Link href="/settings/llm-providers" className="hover:text-foreground transition-colors">
          LLM Providers
        </Link>
        <CaretRight className="h-3.5 w-3.5" weight="bold" />
        <span className="text-foreground font-medium">Add Provider</span>
      </nav>

      <h1 className="text-page-title mb-8">Add Provider</h1>

      <LLMProviderForm onClose={() => router.push('/settings/llm-providers')} />
    </div>
  );
}
