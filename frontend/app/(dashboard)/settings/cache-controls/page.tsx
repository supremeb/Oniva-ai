'use client';

import { CacheControls } from '@/components/settings/CacheControls';

export default function CacheControlsPage() {
  return (
    <div className="p-6 lg:p-8 max-w-5xl">
      <div className="mb-8">
        <h1 className="text-page-title">Cache Controls</h1>
        <p className="text-page-description mt-1">
          Manage application cache and troubleshooting tools
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <CacheControls />
      </div>
    </div>
  );
}
