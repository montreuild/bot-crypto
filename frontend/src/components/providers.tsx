'use client';

import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import { useState, useEffect, ReactNode } from 'react';
import { WebSocketProvider, useWebSocket } from '@/lib/ws-provider';
import { I18nProvider } from '@/lib/i18n';
import { TooltipProvider } from '@radix-ui/react-tooltip';
import { getStoredTheme } from '@/lib/utils';

/** U-03 : le WS invalide le sondage plutôt que l'inverse. */
function WsQueryInvalidator() {
  const qc = useQueryClient();
  const { subscribe } = useWebSocket();
  useEffect(() => {
    return subscribe((event) => {
      if (event.type === 'trade_opened' || event.type === 'trade_closed'
          || event.type === 'cycle.update') {
        qc.invalidateQueries({ queryKey: ['status'] });
        qc.invalidateQueries({ queryKey: ['portfolio'] });
        qc.invalidateQueries({ queryKey: ['trades'] });
      }
    });
  }, [qc, subscribe]);
  return null;
}

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

  // Lot Réglages — l'enregistrement du Service Worker vivait dans un
  // `useEffect` de /settings : un utilisateur qui n'ouvrait jamais les
  // Réglages n'avait jamais de SW, donc pas de PWA installable ni de cache
  // hors-ligne. Il ne devrait pas dépendre de la visite d'une page ; il est
  // remonté ici, au niveau du provider racine.
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js').catch((err) => {
      console.warn('[PWA] Service worker registration failed:', err);
    });
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <WebSocketProvider>
          <WsQueryInvalidator />
          <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
        </WebSocketProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}
