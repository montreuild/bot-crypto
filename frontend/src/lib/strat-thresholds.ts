/**
 * BT-011 — seuils recommandés de `score_threshold` par stratégie.
 *
 * Extrait du template Jinja2 `backtest.html:223` (`STRAT_THRESHOLD`).
 *
 * Valeurs calibrées sur les YAML de stratégies du repo (cf.
 * `strategies/*.yaml → optimizer_results`). Quand une stratégie n'a pas
 * d'entrée ici, aucun threshold warning ne s'affiche.
 */
export const STRAT_THRESHOLDS: Record<string, number> = {
  fear_momentum: 0.72,
  pullback_trend: 0.65,
  trend: 0.6,
  breakout: 0.55,
  supertrend_macd: 0.6,
  smart_money: 0.7,
  smart_trend_adx: 0.65,
  tvr_trend: 0.6,
  momentum_blitz: 0.68,
  snowball_pyramid: 0.62,
  harmonic_regime: 0.6,
  volatility_squeeze: 0.58,
  signal_consensus: 0.62,
  composite_score: 0.6,
  // Opus / ML : le seuil est dynamique, pas statique — pas d'entrée.
};

export function recommendedThreshold(strategy: string): number | null {
  return STRAT_THRESHOLDS[strategy] ?? null;
}
