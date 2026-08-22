/**
 * Verdict d'un backtest : la règle qui décide de ce qu'on dit à l'opérateur.
 *
 * ARCH-02 — ces seuils vivaient au milieu du JSX de `backtest-results.tsx`
 * (708 lignes), donc hors de portée des tests. Ils décident pourtant si une
 * stratégie est présentée comme exploitable, à valider ou à éviter, et
 * `positive` fait apparaître le bouton « Créer le bot ».
 */
import type { BacktestResult, BacktestTrade, StrategyStats } from '@/types';

export type StrategyPanel = StrategyStats & {
  trades?: number | BacktestTrade[];
  walk_forward?: BacktestResult['walk_forward'];
  runs?: BacktestResult['runs'];
  envelope?: unknown;
  realistic_risk_diagnostics?: BacktestResult['realistic_risk_diagnostics'];
  diagnostics?: BacktestResult['diagnostics'];
  ml_info?: BacktestResult['ml_info'];
  equity_final?: number;
  final_equity?: number;
  buy_and_hold_pct?: number;
  alpha?: number;
  alpha_vs_bh?: number;
  initial_capital?: number;
};

export type VerdictTone = 'positive' | 'neutral' | 'negative';

export interface Verdict {
  tone: VerdictTone;
  /** Échantillon trop court ou drawdown trop profond — distingue les deux
   *  messages négatifs : « à éviter en l'état » et « sous-performe ». */
  risky: boolean;
  /** `null` quand aucune stratégie n'a produit de statistiques. */
  bestName: string | null;
  pnl: number;
  winRate: number;
  sharpe: number;
  maxDrawdown: number;
  trades: number;
}

/** Seuils de qualification, réunis pour être lisibles d'un coup d'œil. */
export const SEUILS = {
  sharpeFort: 1.5,
  tradesFort: 20,
  winRateFort: 50,
  drawdownRisque: -20,
  tradesRisque: 10,
} as const;

/** Une passe multi-symboles rend un tableau ; la vue n'affiche que la première. */
export function unwrapBacktest(result: BacktestResult | BacktestResult[]): BacktestResult {
  return Array.isArray(result) ? result[0] : result;
}

export function strategyMap(r: BacktestResult): Record<string, StrategyPanel> {
  return (r?.by_strategy || {}) as Record<string, StrategyPanel>;
}

/** Meilleure stratégie au PnL total. */
export function bestStrategy(
  byStrategy: Record<string, StrategyPanel>,
): readonly [string, StrategyPanel] | null {
  const noms = Object.keys(byStrategy);
  if (noms.length === 0) return null;
  return noms
    .map((name) => [name, byStrategy[name]] as const)
    .sort(([, a], [, b]) => (b.total_pnl ?? 0) - (a.total_pnl ?? 0))[0];
}

/**
 * `risky` l'emporte sur `strong` : un Sharpe flatteur sur 8 trades, ou avec un
 * drawdown de 25 %, ne vaut pas une recommandation.
 */
export function computeVerdict(result: BacktestResult | BacktestResult[]): Verdict {
  const meilleure = bestStrategy(strategyMap(unwrapBacktest(result)));
  if (!meilleure) {
    return { tone: 'neutral', risky: false, bestName: null, pnl: 0,
             winRate: 0, sharpe: 0, maxDrawdown: 0, trades: 0 };
  }
  const [bestName, s] = meilleure;
  const v = {
    bestName,
    pnl: s.total_pnl ?? 0,
    winRate: s.win_rate ?? 0,
    sharpe: s.sharpe ?? 0,
    maxDrawdown: s.max_drawdown ?? 0,
    trades: s.total_trades ?? 0,
  };
  const risque = v.maxDrawdown < SEUILS.drawdownRisque || v.trades < SEUILS.tradesRisque;
  const fort = v.sharpe > SEUILS.sharpeFort
    && v.trades >= SEUILS.tradesFort
    && v.winRate >= SEUILS.winRateFort;

  let tone: VerdictTone = 'negative';
  if (fort && !risque) tone = 'positive';
  else if (v.pnl > 0 && !risque) tone = 'neutral';
  return { tone, risky: risque, ...v };
}
