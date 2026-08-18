import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

vi.mock('@/lib/api', () => ({
  api: {
    getStatus: vi.fn(),
    getHealth: vi.fn(),
    updateTradingConfig: vi.fn(),
  },
}));

import { api } from '@/lib/api';
import { useBotStatus, useHealth, useUpdateTradingConfig } from '@/hooks/use-api';

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function W({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('useBotStatus', () => {
  it('expose le statut renvoyé par l\'API', async () => {
    vi.mocked(api.getStatus).mockResolvedValue({
      status: 'running',
      paper_mode: true,
      timeframe: '1h',
      timeframes: ['1h'],
      strategies: ['trend'],
    } as never);
    const { result } = renderHook(() => useBotStatus(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.status).toBe('running');
    expect(api.getStatus).toHaveBeenCalled();
  });

  it('propage une erreur réseau', async () => {
    vi.mocked(api.getStatus).mockRejectedValue(new Error('down'));
    const { result } = renderHook(() => useBotStatus(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });
});

describe('useHealth', () => {
  it('ajoute client_latency_ms', async () => {
    vi.mocked(api.getHealth).mockResolvedValue({
      status: 'ok', db: true, exchange: true, trader: false,
    } as never);
    const { result } = renderHook(() => useHealth(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.status).toBe('ok');
    expect(typeof result.current.data?.client_latency_ms).toBe('number');
  });
});

describe('useUpdateTradingConfig', () => {
  it('appelle POST paper_mode', async () => {
    vi.mocked(api.updateTradingConfig).mockResolvedValue({
      changed: { paper_mode: false }, saved_to_disk: true, trader_updated: true,
    } as never);
    const { result } = renderHook(() => useUpdateTradingConfig(), { wrapper: wrapper() });
    result.current.mutate({ paper_mode: false });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(api.updateTradingConfig).toHaveBeenCalledWith({ paper_mode: false });
  });
});
