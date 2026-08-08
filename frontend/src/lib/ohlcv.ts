/**
 * F2 — Utilitaires OHLCV partagés (factorisation de 3 copies de cleanOhlcv).
 *
 * Avant cette factorisation, `cleanOhlcv` était copiée à l'identique dans :
 *   - frontend/src/components/views/smart-graph-view.tsx (l. 70)
 *   - frontend/src/components/views/smart-replay-view.tsx (l. 55)
 *   - frontend/src/components/charts/price-signals-chart.tsx (l. 75)
 *
 * Ces 3 copies faisaient exactement la même chose : dédupliquer les
 * timestamps, ignorer les `NaN`, préserver l'ordre chronologique. Toute
 * correction de bug devait être appliquée 3 fois — d'où cette factorisation.
 *
 * Les consommateurs existants peuvent soit :
 *   1. Importer `cleanOhlcv` directement (signature identique) ;
 *   2. Importer le type `CandleRow` pour typage partagé.
 *
 * Migration progressive : les 3 fichiers consommateurs seront migrés un par
 * un (suppression de la copie locale + import depuis `@/lib/ohlcv`) dans les
 * prochaines itérations, sans casser l'existant.
 */

import type { UTCTimestamp } from 'lightweight-charts';

export interface CandleRow {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}

/**
 * Nettoie un tableau OHLCV brut (depuis l'API) pour lightweight-charts.
 *
 * - Ignore les timestamps non finis (NaN, Infinity).
 * - Déduplique les timestamps identiques (garde le premier).
 * - Préserve l'ordre chronologique strict (ignore les timestamps antérieurs
 *   au dernier accepté — utile pour les flux WS qui peuvent renvoyer des
 *   bougies en désordre pendant un gap réseau).
 *
 * @param time  Array de timestamps (secondes, UTC).
 * @param open  Array d'opens.
 * @param high  Array de highs.
 * @param low   Array de lows.
 * @param close Array de closes.
 * @returns     Array de `CandleRow` nettoyé, prêt pour `addCandlestickSeries`.
 */
export function cleanOhlcv(
  time: number[],
  open: number[],
  high: number[],
  low: number[],
  close: number[],
): CandleRow[] {
  const seen = new Set<number>();
  const out: CandleRow[] = [];
  for (let i = 0; i < time.length; i++) {
    const t = time[i];
    if (!Number.isFinite(t)) continue;
    if (seen.has(t)) continue;
    if (out.length > 0 && t < (out[out.length - 1].time as number)) continue;
    seen.add(t);
    out.push({
      time: t as UTCTimestamp,
      open: open[i],
      high: high[i],
      low: low[i],
      close: close[i],
    });
  }
  return out;
}

/**
 * Variant : `cleanOhlcvFromObjects` — accepte un array d'objets OHLCV
 * (au lieu de 5 arrays parallèles). Utile quand l'API renvoie déjà un
 * array de `{ time, open, high, low, close }`.
 */
export function cleanOhlcvFromObjects(
  rows: Array<{ time: number; open: number; high: number; low: number; close: number }>,
): CandleRow[] {
  return cleanOhlcv(
    rows.map((r) => r.time),
    rows.map((r) => r.open),
    rows.map((r) => r.high),
    rows.map((r) => r.low),
    rows.map((r) => r.close),
  );
}
