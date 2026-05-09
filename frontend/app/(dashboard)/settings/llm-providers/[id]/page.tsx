'use client';

import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { CaretRight } from '@phosphor-icons/react';
import { Loader2 } from 'lucide-react';
import { LLMProviderForm } from '@/components/settings/LLMProviderForm';
import { useLLMProvider } from '@/hooks/useLLMProviders';

export default function EditLLMProviderPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: provider, isLoading, error } = useLLMProvider(id);

  if (isLoading) {
    return (
      <div className="p-6 lg:p-8 flex justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !provider) {
    return <div className="p-6 lg:p-8 text-destructive">Provider not found.</div>;
  }

  return (
    <div className="p-6 lg:p-8 max-w-2xl">
      <nav className="flex items-center gap-1.5 text-sm text-muted-foreground mb-6" aria-label="Breadcrumb">
        <Link href="/settings/llm-providers" className="hover:text-foreground transition-colors">
          LLM Providers
        </Link>
        <CaretRight className="h-3.5 w-3.5" weight="bold" />
        <span className="text-foreground font-medium">{provider.display_name}</span>
      </nav>

      <h1 className="text-page-title mb-8">{provider.display_name}</h1>

      <LLMProviderForm
        provider={provider}
        onClose={() => router.push('/settings/llm-providers')}
      />
    </div>
  );
}
