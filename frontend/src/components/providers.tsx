'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, useEffect, ReactNode } from 'react';
import { WebSocketProvider } from '@/lib/ws-provider';
import { I18nProvider } from '@/lib/i18n';
import { TooltipProvider } from '@radix-ui/react-tooltip';
import { getStoredTheme } from '@/lib/utils';

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5 * 1000,
            refetchOnWindowFocus: true,
            retry: 1,
          },
        },
      }),
  );

  // S0-F1-US2 — Applique le thème stocké AVANT paint pour éviter FOUC et
  // permettre au light theme de persister entre les refreshs.
  // Avant : useEffect(() => document.documentElement.classList.add('dark'), [])
  // forçait systématiquement `dark` au mount, écrasant la préférence utilisateur.
  useEffect(() => {
    const theme = getStoredTheme();
    document.documentElement.classList.toggle('dark', theme === 'dark');
    document.documentElement.classList.toggle('light', theme === 'light');
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <WebSocketProvider>
          <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
        </WebSocketProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}
