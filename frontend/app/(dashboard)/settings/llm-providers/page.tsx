'use client';

import Link from 'next/link';
import { Plus } from '@phosphor-icons/react';
import { LLMProvidersSettings } from '@/components/settings/LLMProvidersSettings';

export default function LLMProvidersPage() {
  return (
    <div className="p-6 lg:p-8 max-w-3xl">
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-page-title">LLM Providers</h1>
          <p className="text-page-description mt-1">
            Configure providers and drag to set failover priority.
          </p>
        </div>
        <Link
          href="/settings/llm-providers/new"
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors shrink-0"
        >
          <Plus className="h-4 w-4" />
          Add Provider
        </Link>
      </div>
      <LLMProvidersSettings />
    </div>
  );
}
