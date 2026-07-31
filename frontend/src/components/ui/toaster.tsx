'use client';

import { Toaster as SonnerToaster } from 'sonner';

export function Toaster() {
  return (
    <SonnerToaster
      position="bottom-right"
      theme="dark"
      richColors
      closeButton
      toastOptions={{
        style: {
          background: '#141a23',
          border: '1px solid #1f2937',
          color: '#e5e7eb',
        },
      }}
    />
  );
}
