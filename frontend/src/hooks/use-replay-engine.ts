'use client';

/**
 * RPL-001 — Moteur de replay interactif bougie-par-bougie.
 *
 * Inspired by `smart-replay-view.tsx` playback pattern (setInterval +
 * currentIndex + setData(slice)), adapté pour rejouer les trades d'une
 * stratégie de backtest plutôt que les zones SMC.
 *
 * Extrait du template Jinja2 `replay.html:542-689` (moteur `tick` / `step` /
 * `seekTo` / `setSpeed`).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { BacktestTrade } from '@/types';

export type ReplaySpeed = 0.5 | 1 | 2 | 5 | 10 | 20 | 'MAX';

/** Vitesse → intervalle en ms entre deux bougies. */
const SPEED_MS: Record<Exclude<ReplaySpeed, 'MAX'>, number> = {
  0.5: 2000,
  1: 1000,
  2: 500,
  5: 200,
  10: 100,
  20: 50,
};

export interface CandleRow {
  time: number; // epoch seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface ReplayAccumulatedStats {
  trades: number;
  wins: number;
  pnl: number;
}

export interface SignalLogEntry {
  ts: string;
  side: 'long' | 'short';
  exitPrice: number;
  pnl: number;
  time: number;
}

export interface UseReplayEngineResult {
  position: number;
  total: number;
  isPlaying: boolean;
  speed: ReplaySpeed;
  accumulated: ReplayAccumulatedStats;
  signalLog: SignalLogEntry[];
  /** Index pré-calculés : bar → trades dont l'entrée est à cette bar. */
  entriesByBar: Map<number, BacktestTrade[]>;
  /** Index pré-calculés : bar → trades dont la sortie est à cette bar. */
  exitsByBar: Map<number, BacktestTrade[]>;
  play: () => void;
  pause: () => void;
  togglePlay: () => void;
  step: (delta: number) => void;
  seekTo: (pos: number) => void;
  setSpeed: (s: ReplaySpeed) => void;
  reset: () => void;
}

interface UseReplayEngineParams {
  candles: CandleRow[];
  trades: BacktestTrade[];
}

function buildBarIndex(trades: BacktestTrade[]): {
  entriesByBar: Map<number, BacktestTrade[]>;
  exitsByBar: Map<number, BacktestTrade[]>;
} {
  const entriesByBar = new Map<number, BacktestTrade[]>();
  const exitsByBar = new Map<number, BacktestTrade[]>();
  for (const t of trades) {
    const entryBar = typeof t.bar === 'number' ? t.bar : null;
    const exitBar = typeof t.exit_bar === 'number' ? t.exit_bar : null;
    if (entryBar != null) {
      const arr = entriesByBar.get(entryBar) ?? [];
      arr.push(t);
      entriesByBar.set(entryBar, arr);
    }
    if (exitBar != null) {
      const arr = exitsByBar.get(exitBar) ?? [];
      arr.push(t);
      exitsByBar.set(exitBar, arr);
    }
  }
  return { entriesByBar, exitsByBar };
}

function recomputeStats(
  position: number,
  exitsByBar: Map<number, BacktestTrade[]>,
): { accumulated: ReplayAccumulatedStats; log: SignalLogEntry[] } {
  const accumulated: ReplayAccumulatedStats = { trades: 0, wins: 0, pnl: 0 };
  const log: SignalLogEntry[] = [];
  // Itère dans l'ordre des bars pour garder le log chronologique.
  const sortedBars = Array.from(exitsByBar.keys()).sort((a, b) => a - b);
  for (const bar of sortedBars) {
    if (bar > position) break;
    const tradesAtBar = exitsByBar.get(bar) ?? [];
    for (const t of tradesAtBar) {
      const pnl = t.pnl ?? 0;
      accumulated.trades++;
      if (pnl > 0) accumulated.wins++;
      accumulated.pnl += pnl;
      log.push({
        ts: new Date((t.exit_time ? Date.parse(t.exit_time) : Date.now())).toLocaleTimeString('fr-FR'),
        side: t.side,
        exitPrice: t.exit ?? 0,
        pnl,
        time: bar,
      });
    }
  }
  // Le log est affiché plus récent en tête → on l'inverse.
  log.reverse();
  return { accumulated, log };
}

export function useReplayEngine({
  candles,
  trades,
}: UseReplayEngineParams): UseReplayEngineResult {
  const [position, setPosition] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeedState] = useState<ReplaySpeed>(1);
  const [accumulated, setAccumulated] = useState<ReplayAccumulatedStats>({
    trades: 0,
    wins: 0,
    pnl: 0,
  });
  const [signalLog, setSignalLog] = useState<SignalLogEntry[]>([]);

  const { entriesByBar, exitsByBar } = useMemo(() => buildBarIndex(trades), [trades]);
  const total = candles.length;
  const speedRef = useRef(speed);
  speedRef.current = speed;

  // Quand les données changent, reset position + stats.
  useEffect(() => {
    setPosition(0);
    setIsPlaying(false);
    setAccumulated({ trades: 0, wins: 0, pnl: 0 });
    setSignalLog([]);
  }, [candles, trades]);

  // Clamp position si elle dépasse le total (changement de données).
  useEffect(() => {
    if (total > 0) {
      setPosition((p) => Math.max(0, Math.min(p, total - 1)));
    } else {
      setPosition(0);
    }
  }, [total]);

  // Recalcule les stats accumulées quand la position change (ou les trades).
  // On ne recompute QUE les exits ≤ position, et on garde le log trié.
  useEffect(() => {
    if (total === 0) return;
    const { accumulated: acc, log } = recomputeStats(position, exitsByBar);
    setAccumulated(acc);
    setSignalLog(log);
  }, [position, exitsByBar, total]);

  // Boucle de playback.
  useEffect(() => {
    if (!isPlaying) return;
    if (speed === 'MAX') {
      // Mode MAX : batch via requestAnimationFrame, 100 bougies par frame.
      let raf: number;
      const tick = () => {
        setPosition((p) => {
          const next = Math.min(total - 1, p + 100);
          if (next >= total - 1) {
            setIsPlaying(false);
            return total - 1;
          }
          return next;
        });
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }
    const ms = SPEED_MS[speed];
    const id = setInterval(() => {
      setPosition((p) => {
        const next = p + 1;
        if (next >= total - 1) {
          setIsPlaying(false);
          return total - 1;
        }
        return next;
      });
    }, ms);
    return () => clearInterval(id);
  }, [isPlaying, speed, total]);

  const play = useCallback(() => {
    if (total === 0) return;
    setPosition((p) => (p >= total - 1 ? 0 : p));
    setIsPlaying(true);
  }, [total]);

  const pause = useCallback(() => setIsPlaying(false), []);

  const togglePlay = useCallback(() => {
    if (isPlaying) {
      pause();
    } else {
      play();
    }
  }, [isPlaying, play, pause]);

  const step = useCallback(
    (delta: number) => {
      setIsPlaying(false);
      setPosition((p) => Math.max(0, Math.min(total - 1, p + delta)));
    },
    [total],
  );

  const seekTo = useCallback(
    (pos: number) => {
      setIsPlaying(false);
      setPosition(Math.max(0, Math.min(total - 1, Math.floor(pos))));
    },
    [total],
  );

  const setSpeed = useCallback((s: ReplaySpeed) => {
    setSpeedState(s);
  }, []);

  const reset = useCallback(() => {
    setIsPlaying(false);
    setPosition(0);
  }, []);

  return {
    position,
    total,
    isPlaying,
    speed,
    accumulated,
    signalLog,
    entriesByBar,
    exitsByBar,
    play,
    pause,
    togglePlay,
    step,
    seekTo,
    setSpeed,
    reset,
  };
}
