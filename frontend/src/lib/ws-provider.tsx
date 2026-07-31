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

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setStatus('connecting');
    // S1-04/S1-05 : plus de clé API dans l'URL — le cookie HttpOnly api_key
    // (posé par les pages web) est envoyé automatiquement par le navigateur
    // lors du handshake WebSocket, sans exposition dans les logs/devtools.
    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
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
        console.error('[WS] error:', e);
        setStatus('error');
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setStatus('disconnected');
        wsRef.current = null;
        const attempt = reconnectAttempts.current++;
        const delay = Math.min(1000 * 2 ** attempt, 30000);
        console.info(`[WS] reconnecting in ${delay}ms (attempt ${attempt + 1})`);
        reconnectTimeoutRef.current = setTimeout(connect, delay);
      };
    } catch (err) {
      console.error('[WS] connect error:', err);
      setStatus('error');
      reconnectTimeoutRef.current = setTimeout(connect, 5000);
    }
  }, []);

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
