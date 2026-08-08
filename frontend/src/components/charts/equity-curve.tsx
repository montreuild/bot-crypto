'use client';

/**
 * S0-F1-US1 — EquityCurve réelle (wrapper vers <EquityChart variant="live" />).
 *
 * F3 (refactor) : le composant est désormais un wrapper fin qui délègue à
 * `<EquityChart>` (frontend/src/components/charts/equity-chart.tsx). L'API
 * publique (`<EquityCurve />` sans props) est préservée — les consommateurs
 * de `/portfolio` n'ont pas besoin de modification.
 *
 * Avant : ce fichier contenait ~155 lignes incluant AreaChart, gradient,
 * CartesianGrid, formatUSD, tooltip, gestion empty/loading/error — toute
 * cette logique est désormais dans `<EquityChart>` et partagée avec la
 * variante backtest (anciennement `backtest-equity-chart.tsx`).
 *
 * Historique (S0-F1-US1) : avant ce composant affichait des données sin/cos
 * simulées (TODO mentionnait `/api/stats/daily` non câblé). Le KPI le plus
 * regardé par un trader était donc trompeur. Désormais : consomme
 * `useDailyStats(30)` via `<EquityChart variant="live" />`.
 */

import { useBotStatus, useDailyStats } from '@/hooks/use-api';
import { EquityChart } from '@/components/charts/equity-chart';

export function EquityCurve() {
  const { data: status } = useBotStatus();
  const { data: dailyStats, isLoading, isError } = useDailyStats(30);

  return (
    <EquityChart
      variant="live"
      dailyData={dailyStats as any}
      currentCapital={status?.capital}
      currentPnl={status?.total_pnl}
      isLoading={isLoading}
      isError={isError}
    />
  );
}
