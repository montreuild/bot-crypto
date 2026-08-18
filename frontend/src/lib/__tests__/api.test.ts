import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiError, isBackendUnreachable, apiFetch } from '@/lib/api';

describe('ApiError', () => {
  it('marque un backend injoignable par status 0', () => {
    const err = new ApiError(0, 'down');
    expect(isBackendUnreachable(err)).toBe(true);
    expect(isBackendUnreachable(new ApiError(500, 'x'))).toBe(false);
    expect(isBackendUnreachable(new Error('x'))).toBe(false);
  });
});

describe('apiFetch', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('lève ApiError 0 si fetch échoue', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(apiFetch('/status')).rejects.toBeInstanceOf(ApiError);
    try {
      await apiFetch('/status');
    } catch (e) {
      expect(isBackendUnreachable(e)).toBe(true);
    }
  });

  it('lève ApiError avec le status HTTP', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      statusText: 'Forbidden',
      json: async () => ({ detail: 'clé invalide' }),
    }));
    await expect(apiFetch('/config')).rejects.toMatchObject({ status: 403 });
  });

  it('renvoie le JSON si ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    }));
    await expect(apiFetch<{ status: string }>('/health')).resolves.toEqual({ status: 'ok' });
  });

  it('traite 204 comme undefined', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      json: async () => { throw new Error('no body'); },
    }));
    await expect(apiFetch('/thing')).resolves.toBeUndefined();
  });

  it('ramène un 503 « injoignable » à status 0', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      json: async () => ({ detail: 'Backend injoignable' }),
    }));
    try {
      await apiFetch('/status');
      expect.unreachable();
    } catch (e) {
      expect(isBackendUnreachable(e)).toBe(true);
    }
  });

  it('ne bloque pas si le schéma Zod échoue', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ unexpected: true }),
    }));
    const schema = {
      safeParse: () => ({ success: false as const, error: { issues: [] } }),
    };
    await expect(apiFetch('/x', { schema })).resolves.toEqual({ unexpected: true });
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('envoie credentials include et pas de Content-Type sans corps', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({}),
    });
    vi.stubGlobal('fetch', fetchMock);
    await apiFetch('/status');
    const [, init] = fetchMock.mock.calls[0];
    expect(init.credentials).toBe('include');
    expect(init.headers['Content-Type']).toBeUndefined();
  });
});

describe('api helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('getTrades encode les filtres', async () => {
    const { api } = await import('@/lib/api');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ total: 0, offset: 0, limit: 10, trades: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await api.getTrades({ limit: 10, symbol: 'BTC/USDC', strategy: 'trend' });
    expect(fetchMock.mock.calls[0][0]).toContain('/api/trades?');
    expect(fetchMock.mock.calls[0][0]).toContain('symbol=BTC');
    expect(fetchMock.mock.calls[0][0]).toContain('strategy=trend');
  });

  it('updateTradingConfig POST un corps JSON', async () => {
    const { api } = await import('@/lib/api');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ changed: {}, saved_to_disk: true, trader_updated: false }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await api.updateTradingConfig({ paper_mode: false });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/config/trading');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ paper_mode: false });
    expect(init.headers['Content-Type']).toBe('application/json');
  });
});
