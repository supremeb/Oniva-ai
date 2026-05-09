'use client';

import { Toaster as Sonner } from 'sonner';

export function Toaster() {
  return (
    <Sonner
      position="bottom-right"
      theme="system"
      richColors
      closeButton
      expand
      visibleToasts={3}
      duration={5000}
      className="toaster group"
    />
  );
}
