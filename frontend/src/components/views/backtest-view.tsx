/**
 * P2-5 : BacktestTab — extraction planifiée de lab/page.tsx.
 *
 * ``BacktestTab`` fait ~1200 lignes dans ``lab/page.tsx`` (1481 lignes total).
 * L'extraction complète nécessite de déplacer tous les imports, hooks et
 * sous-composants (Verdict, warnings, KPIs, etc.) vers ce fichier.
 *
 * Étapes du découpage (à faire dans une PR dédiée) :
 * 1. Extraire ``Verdict`` vers ``components/cards/backtest-verdict.tsx``
 * 2. Extraire ``BacktestResults`` vers ``components/views/backtest-results.tsx``
 * 3. Extraire ``BacktestTab`` (le formulaire + orchestration) vers ce fichier
 * 4. ``lab/page.tsx`` ne contient plus que ``LabPage`` (layout + onglets)
 *
 * Ce fichier est un placeholder qui documente le plan.
 */
export const BACKTEST_TAB_EXTRACTION_PLAN = `
BacktestTab extraction plan (P2-5):

1. Verdict → components/cards/backtest-verdict.tsx
2. BacktestResults → components/views/backtest-results.tsx
3. BacktestTab → components/views/backtest-view.tsx (ce fichier)
4. lab/page.tsx → LabPage only (~150 lines)

Current state: BacktestTab is inline in lab/page.tsx (lines 264-1481).
Dependencies: useBacktestSettings, useRunBacktest, useCancelBacktest,
useBacktestStatus, useBacktestSession, useTradingTimeframes, api, toast,
BacktestEquityChart, PriceSignalsChart, TradesTable, TradesStatsPanel,
DiagnosticsPanel, RecommendationsPanel, CostSimulatorPanel, MonteCarloPanel,
WalkForwardTable, TradesScatter, MLBacktestPanel, StrategyComparisonTable,
CostModelCard, BacktestRunningBanner, BacktestProgress.
`;
