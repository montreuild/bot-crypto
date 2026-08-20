/**
 * Timeframes de trading activés (source unique : config / status API).
 * Remplace les listes hardcodées dispersées dans Scanner, Lab, Smart Graph…
 */

'use client';

import { useMemo } from 'react';
import { useBotStatus, useConfig } from '@/hooks/use-api';

const FALLBACK = ['15m', '30m', '1h', '4h', '1d'] as const;

/**
 * Retourne les TF actifs, le défaut (premier TF), et un helper de validation.
 */
export function useTradingTimeframes(preferredDefault?: string) {
  const { data: status } = useBotStatus();
  const { data: config } = useConfig();

  const timeframes = useMemo(() => {
    const fromStatus = status?.timeframes;
    if (Array.isArray(fromStatus) && fromStatus.length > 0) {
      return fromStatus.map(String);
    }
    const fromCfg = (config as { trading?: { timeframes?: string[] } } | undefined)?.trading?.timeframes;
    if (Array.isArray(fromCfg) && fromCfg.length > 0) {
      return fromCfg.map(String);
    }
    return [...FALLBACK];
  }, [status, config]);

  const defaultTf = useMemo(() => {
    if (preferredDefault && timeframes.includes(preferredDefault)) {
      return preferredDefault;
    }
    const single = (config as { trading?: { timeframe?: string } } | undefined)?.trading?.timeframe
      || status?.timeframe;
    if (single && timeframes.includes(String(single))) return String(single);
    return timeframes[0] || '1h';
  }, [timeframes, preferredDefault, config, status]);

  return { timeframes, defaultTf, isReady: Boolean(status || config) };
}
