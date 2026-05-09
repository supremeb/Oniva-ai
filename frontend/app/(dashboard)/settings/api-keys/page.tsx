'use client';

import { useState } from 'react';
import { Plus } from '@phosphor-icons/react';
import { APIKeyList } from '@/components/settings/APIKeyList';
import { APIKeyForm } from '@/components/settings/APIKeyForm';

export default function APIKeysPage() {
  const [showForm, setShowForm] = useState(false);

  return (
    <div className="p-6 lg:p-8 max-w-5xl">
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-page-title">API Keys</h1>
          <p className="text-page-description mt-1">
            Manage your LLM provider API keys
          </p>
        </div>
        <button
          onClick={() => setShowForm(true)}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors shrink-0"
        >
          <Plus className="h-4 w-4" />
          Add Key
        </button>
      </div>
      {showForm && <APIKeyForm onClose={() => setShowForm(false)} />}
      <APIKeyList />
    </div>
  );
}
