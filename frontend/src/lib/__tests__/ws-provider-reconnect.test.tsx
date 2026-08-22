/**
 * FE-02 — pas de handshake WebSocket sans jeton.
 *
 * Le jeton est à usage unique et vit 30 s : en redemander un à chaque
 * tentative est correct. Mais quand le POST échouait, `connect()` ouvrait
 * quand même la socket sur l'URL nue, qui se faisait refuser en 4403 : deux
 * allers-retours perdus par cycle au lieu d'un, et un `[WS] error` qui
 * masquait la vraie cause.
 */
import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { WebSocketProvider } from '@/lib/ws-provider';

const sockets: string[] = [];

class _FakeWS {
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) { sockets.push(url); }
  send() {}
  close() {}
}

beforeEach(() => {
  sockets.length = 0;
  vi.stubGlobal('WebSocket', _FakeWS);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** `waitFor` sonde en temps réel : sous faux timers, on vide la file à la main. */
async function _flush() {
  await vi.advanceTimersByTimeAsync(0);
}

function _monter(ticket: string | null, ok = true) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok, json: async () => ({ ticket }) })));
  render(<WebSocketProvider><div /></WebSocketProvider>);
}

describe('WSProvider — reconnexion', () => {
  it('n’ouvre aucune socket quand le jeton manque', async () => {
    _monter(null);
    await _flush();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(sockets).toEqual([]);
  });

  it('n’ouvre aucune socket quand le POST échoue', async () => {
    _monter('inutilisé', false);
    await _flush();
    expect(sockets).toEqual([]);
  });

  it('ouvre la socket avec le ticket quand il est là', async () => {
    _monter('abc123');
    await _flush();
    expect(sockets).toHaveLength(1);
    expect(sockets[0]).toContain('ticket=abc123');
  });

  it('replanifie avec un recul croissant sans ouvrir de socket', async () => {
    _monter(null);
    await _flush();
    expect(fetch).toHaveBeenCalledTimes(1);

    for (const [i, delai] of [1000, 2000, 4000].entries()) {
      await vi.advanceTimersByTimeAsync(delai);
      expect(fetch).toHaveBeenCalledTimes(i + 2);
    }
    expect(sockets).toEqual([]);
  });
});
