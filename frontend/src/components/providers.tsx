'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, useEffect, ReactNode } from 'react';
import { WebSocketProvider } from '@/lib/ws-provider';
import { TooltipProvider } from '@radix-ui/react-tooltip';

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

  useEffect(() => {
    document.documentElement.classList.add('dark');
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <WebSocketProvider>
        <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
      </WebSocketProvider>
    </QueryClientProvider>
  );
}
