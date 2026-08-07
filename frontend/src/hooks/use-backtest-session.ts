/**
 * BT-004 — sync serveur + persistance session pour le backtest.
 *
 * - `useBacktestStatus` : poll `/api/backtest/status` toutes les 5 s pour
 *   détecter un backtest en cours côté serveur (autre onglet, post-reload).
 * - `useBacktestSession` : save/load du dernier résultat dans `sessionStorage`
 *   (restauration après reload accidentel, TTL 30 min).
 */

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';

export interface BacktestStatus {
  running: boolean;
  startedAt: string | null;
}

/** Poll `/api/backtest/status` toutes les 5 s. */
export function useBacktestStatus(): BacktestStatus {
  const { data } = useQuery({
    queryKey: ['backtest-status'],
    queryFn: async () => {
      try {
        const r = await apiFetch<{ running?: boolean; started_at?: string; startedAt?: string }>(
          '/backtest/status',
        );
        return {
          running: !!r.running,
          startedAt: r.started_at ?? r.startedAt ?? null,
        } as BacktestStatus;
      } catch {
        return { running: false, startedAt: null } as BacktestStatus;
      }
    },
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  });
  return data ?? { running: false, startedAt: null };
}

const SESSION_KEY = 'bt_data';
const SESSION_TTL_MS = 30 * 60 * 1000; // 30 minutes

export interface BacktestSessionData {
  result: any;
  config: {
    symbol: string;
    timeframe: string;
    limit: number;
    strategies: string[];
    walk_forward?: boolean;
    monte_carlo?: boolean;
    dual_pass?: boolean;
  };
  timestamp: number;
}

/** Save/load du dernier résultat dans `sessionStorage`. */
export function useBacktestSession() {
  const [restored, setRestored] = useState<BacktestSessionData | null>(null);

  // Au mount, tenter de restaurer.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      if (!raw) return;
      const s = JSON.parse(raw) as BacktestSessionData;
      if (!s || !s.result) return;
      if (Date.now() - s.timestamp > SESSION_TTL_MS) {
        sessionStorage.removeItem(SESSION_KEY);
        return;
      }
      setRestored(s);
    } catch {
      // ignore
    }
  }, []);

  const save = (result: any, config: BacktestSessionData['config']) => {
    try {
      const data: BacktestSessionData = {
        result,
        config,
        timestamp: Date.now(),
      };
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(data));
    } catch {
      // sessionStorage peut être plein ou bloqué — on ignore.
    }
  };

  const clear = () => {
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      // ignore
    }
    setRestored(null);
  };

  return { restored, save, clear };
}
