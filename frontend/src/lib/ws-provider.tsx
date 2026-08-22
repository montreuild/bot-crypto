/**
 * Client WebSocket temps réel avec reconnexion auto (backoff exponentiel max 30s).
 */

'use client';

import { useEffect, useRef, useState, useCallback, createContext, useContext, ReactNode } from 'react';
import type { WSEvent, TradeOpenedData, TradeClosedData, SignalData, RiskData, CycleUpdateData, TickerData } from '@/types';

/**
 * S0-F1-US6 — Résolution de l'URL WebSocket.
 *
 * Priorité :
 * 1. `NEXT_PUBLIC_WS_URL` définie explicitement (dev = ws://localhost:8000/ws)
 * 2. Fallback same-origin runtime : si la page est sur https://example.com,
 *    le WS sera wss://example.com/ws — pattern recommandé en prod.
 * 3. Dernier fallback : ws://localhost:8000/ws (dev local sans config).
 */
function resolveWsUrl(): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  if (configured) return configured;

  if (typeof window !== 'undefined') {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws`;
  }

  return 'ws://localhost:8000/ws';
}

const WS_URL = resolveWsUrl();

/** Demande un jeton WS éphémère via le proxy same-origin (pose aussi le cookie). */
async function fetchWsTicket(): Promise<string | null> {
  try {
    const r = await fetch('/api/ws/ticket', { method: 'POST', credentials: 'include' });
    if (!r.ok) return null;
    const body = await r.json();
    return typeof body?.ticket === 'string' && body.ticket ? body.ticket : null;
  } catch {
    return null;
  }
}

export function wsUrlWithTicket(base: string, ticket: string | null): string {
  if (!ticket) return base;
  try {
    const origin = typeof window !== 'undefined' ? window.location.href : 'http://localhost';
    const u = new URL(base, origin);
    u.searchParams.set('ticket', ticket);
    return u.toString();
  } catch {
    const sep = base.includes('?') ? '&' : '?';
    return `${base}${sep}ticket=${encodeURIComponent(ticket)}`;
  }
}

type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

interface WSContextValue {
  status: ConnectionStatus;
  lastEvent: WSEvent | null;
  subscribe: (handler: (event: WSEvent) => void) => () => void;
  subscribersCount: number;
}

const WSContext = createContext<WSContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const [lastEvent, setLastEvent] = useState<WSEvent | null>(null);
  const [subscribersCount, setSubscribersCount] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Set<(event: WSEvent) => void>>(new Set());
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttempts = useRef(0);
  const mountedRef = useRef(true);
  const connectingRef = useRef(false);
  // `scheduleReconnect` est defini avant `connect` et le rappelle : la ref
  // casse le cycle sans remettre `connect` dans ses deps.
  const connectRef = useRef<(() => void) | null>(null);

  /** Recul exponentiel borne a 30 s, compteur remis a zero par `onopen`. */
  const scheduleReconnect = useCallback(() => {
    if (!mountedRef.current) return;
    const attempt = reconnectAttempts.current++;
    const delay = Math.min(1000 * 2 ** attempt, 30000);
    console.info(`[WS] reconnecting in ${delay}ms (attempt ${attempt + 1})`);
    reconnectTimeoutRef.current = setTimeout(() => connectRef.current?.(), delay);
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    if (connectingRef.current) return;

    connectingRef.current = true;
    setStatus('connecting');
    // Le cookie HttpOnly n'existe qu'après un premier aller-retour REST via
    // le proxy. Sans jeton, le handshake /ws partait en 403 (Disconnected
    // sur le premier écran). POST /api/ws/ticket pose le cookie ET fournit
    // un ticket à usage unique, prévu pour ça (SEC-02).
    void (async () => {
      const ticket = await fetchWsTicket();
      if (!mountedRef.current) {
        connectingRef.current = false;
        return;
      }
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        connectingRef.current = false;
        return;
      }
      // FE-02 : sans jeton, le handshake part sur l'URL nue et se fait
      // refuser en 4403. Deux allers-retours perdus par cycle au lieu d'un,
      // et un `[WS] error` qui masque la vraie cause (le POST a echoue).
      if (!ticket) {
        connectingRef.current = false;
        setStatus('disconnected');
        scheduleReconnect();
        return;
      }
      try {
        const ws = new WebSocket(wsUrlWithTicket(WS_URL, ticket));
        wsRef.current = ws;

        ws.onopen = () => {
          connectingRef.current = false;
          if (!mountedRef.current) return;
          reconnectAttempts.current = 0;
          setStatus('connected');
          console.info('[WS] connected');
        };

        ws.onmessage = (e) => {
          try {
            const event: WSEvent = JSON.parse(e.data);
            setLastEvent(event);
            if (event.type === 'connected') {
              setSubscribersCount(event.data?.subscribers || 0);
            }
            handlersRef.current.forEach((h) => {
              try { h(event); } catch (err) { console.error('[WS] handler error:', err); }
            });
          } catch (err) {
            console.error('[WS] parse error:', err);
          }
        };

        ws.onerror = (e) => {
          connectingRef.current = false;
          console.error('[WS] error:', e);
          setStatus('error');
        };

        ws.onclose = () => {
          connectingRef.current = false;
          if (!mountedRef.current) return;
          setStatus('disconnected');
          wsRef.current = null;
          scheduleReconnect();
        };
      } catch (err) {
        connectingRef.current = false;
        console.error('[WS] connect error:', err);
        setStatus('error');
        scheduleReconnect();
      }
    })();
  }, [scheduleReconnect]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        try { wsRef.current.send(JSON.stringify({ type: 'ping' })); } catch {}
      }
    }, 30000);

    return () => {
      mountedRef.current = false;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      clearInterval(pingInterval);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [connect]);

  const subscribe = useCallback((handler: (event: WSEvent) => void) => {
    handlersRef.current.add(handler);
    return () => { handlersRef.current.delete(handler); };
  }, []);

  return (
    <WSContext.Provider value={{ status, lastEvent, subscribe, subscribersCount }}>
      {children}
    </WSContext.Provider>
  );
}

export function useWebSocket() {
  const ctx = useContext(WSContext);
  if (!ctx) throw new Error('useWebSocket must be used within WebSocketProvider');
  return ctx;
}

// ── Hooks spécialisés ───────────────────────────────────────────────────────

export function useTradeEvents() {
  const { subscribe } = useWebSocket();
  const [openedTrades, setOpenedTrades] = useState<WSEvent<TradeOpenedData>[]>([]);
  const [closedTrades, setClosedTrades] = useState<WSEvent<TradeClosedData>[]>([]);

  useEffect(() => {
    const unsub = subscribe((event) => {
      if (event.type === 'trade.opened') {
        setOpenedTrades((prev) => [event as WSEvent<TradeOpenedData>, ...prev].slice(0, 50));
      } else if (event.type === 'trade.closed') {
        setClosedTrades((prev) => [event as WSEvent<TradeClosedData>, ...prev].slice(0, 50));
      }
    });
    return unsub;
  }, [subscribe]);

  return { openedTrades, closedTrades };
}

export function useSignalEvents() {
  const { subscribe } = useWebSocket();
  const [signals, setSignals] = useState<WSEvent<SignalData>[]>([]);

  useEffect(() => {
    const unsub = subscribe((event) => {
      if (event.type === 'signal.generated') {
        setSignals((prev) => [event as WSEvent<SignalData>, ...prev].slice(0, 100));
      }
    });
    return unsub;
  }, [subscribe]);

  return signals;
}

export function useRiskEvents() {
  const { subscribe } = useWebSocket();
  const [risks, setRisks] = useState<WSEvent<RiskData>[]>([]);

  useEffect(() => {
    const unsub = subscribe((event) => {
      if (event.type.startsWith('risk.')) {
        setRisks((prev) => [event as WSEvent<RiskData>, ...prev].slice(0, 20));
      }
    });
    return unsub;
  }, [subscribe]);

  return risks;
}

export function useCycleUpdates() {
  const { subscribe } = useWebSocket();
  const [cycle, setCycle] = useState<WSEvent<CycleUpdateData> | null>(null);

  useEffect(() => {
    const unsub = subscribe((event) => {
      if (event.type === 'cycle.update') {
        setCycle(event as WSEvent<CycleUpdateData>);
      }
    });
    return unsub;
  }, [subscribe]);

  return cycle;
}

export function useLiveTickers() {
  const { subscribe } = useWebSocket();
  const [tickers, setTickers] = useState<Record<string, TickerData>>({});

  useEffect(() => {
    const unsub = subscribe((event) => {
      if (event.type === 'ticker.update') {
        const data = event.data as TickerData;
        setTickers((prev) => ({ ...prev, [data.symbol]: data }));
      }
    });
    return unsub;
  }, [subscribe]);

  return tickers;
}
