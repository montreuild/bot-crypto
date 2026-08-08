'use client';

/**
 * E3-F2-US6 — Courbe d'équité du backtest avec Buy & Hold superposé
 * (wrapper vers <EquityChart variant="backtest" />).
 *
 * F3 (refactor) : le composant est désormais un wrapper fin qui délègue à
 * `<EquityChart>` (frontend/src/components/charts/equity-chart.tsx). L'API
 * publique (`<BacktestEquityChart equityCurve={...} initialCapital={...}
 * buyAndHoldPnl={...} alpha={...} strategy={...} />`) est préservée — les
 * consommateurs de `/lab` n'ont pas besoin de modification.
 *
 * Avant : ce fichier contenait ~164 lignes incluant ComposedChart, Area, Line,
 * ReferenceLine, gradient, CartesianGrid, formatUSD, tooltip, gestion empty —
 * toute cette logique est désormais dans `<EquityChart>` et partagée avec la
 * variante live (anciennement `equity-curve.tsx`).
 *
 * Historique (E3-F2-US6) : `/lab` n'affichait AUCUNE courbe d'équité pour un
 * résultat de backtest (juste des cartes de KPI), alors que le backend
 * renvoie déjà tout le nécessaire par stratégie. `equity_curve` est une série
 * de capitaux SANS horodatage (un point par trade clôturé) — l'axe X est donc
 * l'index de trade, pas une date. Le Buy & Hold n'existe que comme SCALAIRE
 * (`buy_and_hold_pnl`) : il est tracé en droite du capital initial vers
 * capital + B&H (cf. <EquityChart> pour l'implémentation).
 */

import { EquityChart } from '@/components/charts/equity-chart';

export interface BacktestEquityProps {
  equityCurve?: number[];
  initialCapital?: number;
  buyAndHoldPnl?: number | null;
  alpha?: number | null;
  strategy?: string;
}

export function BacktestEquityChart({
  equityCurve,
  initialCapital,
  buyAndHoldPnl,
  alpha,
  strategy,
}: BacktestEquityProps) {
  return (
    <EquityChart
      variant="backtest"
      equityCurve={equityCurve}
      initialCapital={initialCapital}
      buyAndHoldPnl={buyAndHoldPnl}
      alpha={alpha}
      strategy={strategy}
      showFullscreen
    />
  );
}
