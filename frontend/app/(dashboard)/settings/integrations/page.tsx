'use client';

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

export default function IntegrationsPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace('/settings/integrations/connectors');
  }, [router]);
  return null;
}
