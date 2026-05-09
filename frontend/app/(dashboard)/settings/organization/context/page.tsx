'use client';

import { OrgContextSettings } from '@/components/settings/OrgContextSettings';

export default function OrgContextPage() {
  return (
    <div className="p-6 lg:p-8 max-w-5xl">
      <div className="mb-8">
        <h1 className="text-page-title">Organization Context</h1>
        <p className="text-page-description mt-1">
          Configure the org profile and shared knowledge that gets injected into every agent prompt
        </p>
      </div>
      <OrgContextSettings />
    </div>
  );
}
