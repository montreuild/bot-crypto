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
});
