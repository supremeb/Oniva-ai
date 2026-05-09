'use client';

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

export default function SettingsPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace('/settings/llm-providers');
  }, [router]);
  return null;
}
