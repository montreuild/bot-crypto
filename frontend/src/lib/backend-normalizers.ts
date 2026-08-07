/**
 * Helpers pour normaliser les réponses backend vers les types frontend.
 *
 * Le backend utilise parfois des noms de champs différents de ceux du doc
 * de specs (cf. audit backend §1.6, §1.10, §3.3, §5). Ces helpers font
 * la translation sans modifier le backend.
 */

import type {
  BacktestDiagnostics,
  BacktestResult,
  BacktestTrade,
  OptimizeJob,
  OptimizeTrial,
} from '@/types';

/** BT-002 — normalise un trade backend vers un trade frontend.
 *  Garantit que `signal_reason` et `exit_reason` sont toujours remplis. */
export function normalizeTrade(t: BacktestTrade): BacktestTrade {
  return {
    ...t,
    signal_reason: t.signal_reason ?? t.reason ?? '',
    exit_reason: t.exit_reason ?? t.reason ?? '',
    // Si `stop_initial` absent, déduire de `stop` (le stop initial est souvent
    // le premier point du stop_trail, ou directement `stop` si pas de trailing).
    stop_initial: t.stop_initial ?? t.stop,
  };
}

/** BT-003 — `per_strategy` en tableau, quelle que soit la forme reçue.
 *
 *  Le backend écrit un DICT `{nom: stats}` (`backtest.py::per_strategy_stats`,
 *  typé `Dict[str, Dict[str, int]]`), alors que le front attend un tableau.
 *  Un dict n'ayant pas de `.length`, la garde `per_strategy.length > 0` du
 *  panneau était toujours fausse : le tableau par stratégie ne s'est jamais
 *  affiché depuis son ajout. On accepte les deux formes plutôt que de parier
 *  sur l'une d'elles. */
export function normalizePerStrategy(
  per: unknown,
): NonNullable<BacktestDiagnostics['per_strategy']> {
  if (!per) return [];
  if (Array.isArray(per)) {
    return per.filter((e) => e && typeof e === 'object');
  }
  if (typeof per === 'object') {
    return Object.entries(per as Record<string, any>)
      .filter(([, stats]) => stats && typeof stats === 'object')
      .map(([strategy, stats]) => ({ strategy, ...(stats as Record<string, number>) }));
  }
  return [];
}

/** BT-003 — normalise `diagnostics` (alias backend → frontend). */
export function normalizeDiagnostics(
  d: BacktestDiagnostics | Record<string, any> | null | undefined,
): BacktestDiagnostics | null {
  if (!d || typeof d !== 'object') return null;
  const raw = d as Record<string, any>;
  return {
    bars_total: raw.bars_total ?? raw.barsTotal,
    bars_in_position: raw.bars_in_position ?? raw.barsInPosition,
    signals_accepted: raw.signals_accepted ?? raw.signal_accepted ?? 0,
    trades_opened: raw.trades_opened ?? raw.tradesOpened ?? 0,
    rejections: {
      notional: raw.rejections?.notional ?? raw.rejected_notional ?? 0,
      atr_le_zero: raw.rejections?.atr_le_zero ?? raw.rejected_atr_zero ?? 0,
    },
    max_bars_in_position: raw.max_bars_in_position ?? raw.maxBarsInPosition,
    max_bars_without_signal: raw.max_bars_without_signal ?? raw.max_bars_no_signal,
    per_strategy: normalizePerStrategy(raw.per_strategy ?? raw.perStrategy),
  };
}

/** BT-010 — normalise `ml_info` (le backend renvoie un dict par modèle). */
export function normalizeMlInfo(
  mlInfo: any,
  strategy: string,
): {
  auc: number | null;
  n_features: number | null;
  lookahead: number | null;
  proba_up: number | null;
  model_version: string | null;
} | null {
  if (!mlInfo || typeof mlInfo !== 'object') return null;
  // Si c'est un dict par modèle (cas backend), prendre le premier ou celui
  // qui matche la stratégie.
  if (Array.isArray(mlInfo)) {
    const entry = mlInfo.find((m: any) => m?.strategy === strategy) ?? mlInfo[0];
    if (!entry) return null;
    return {
      auc: entry.auc ?? null,
      n_features: entry.n_features ?? entry.nFeatures ?? null,
      lookahead: entry.lookahead ?? null,
      proba_up: entry.proba_up ?? entry.probaUp ?? null,
      model_version: entry.version_id ?? entry.model_version ?? null,
    };
  }
  // Si c'est déjà un objet plat (frontend-friendly).
  return {
    auc: mlInfo.auc ?? null,
    n_features: mlInfo.n_features ?? mlInfo.nFeatures ?? null,
    lookahead: mlInfo.lookahead ?? null,
    proba_up: mlInfo.proba_up ?? mlInfo.probaUp ?? null,
    model_version: mlInfo.version_id ?? mlInfo.model_version ?? null,
  };
}

/** OPT-001 — reconstruit le bloc `after` depuis `best_oos_*` si le backend
 *  ne le fournit pas directement (cf. audit §3.2). */
export function deriveAfter(job: OptimizeJob): {
  trades: number;
  pnl: number;
  sharpe: number;
  win_rate: number;
  max_drawdown: number;
  alpha: number;
} | null {
  if (!job.result) return null;
  if (job.result.after) return job.result.after;
  const r = job.result;
  // Si on a au moins un des champs best_oos_*, on peut reconstruire.
  if (
    r.best_oos_trades == null &&
    r.best_oos_pnl == null &&
    r.best_oos_sharpe == null
  ) {
    return null;
  }
  return {
    trades: r.best_oos_trades ?? 0,
    pnl: r.best_oos_pnl ?? 0,
    sharpe: r.best_oos_sharpe ?? 0,
    win_rate: r.best_oos_wr ?? 0,
    max_drawdown: r.best_oos_dd ?? 0,
    alpha: r.best_oos_alpha ?? 0,
  };
}

/** OPT-001 — normalise `baseline` (alias backend `wr`→`win_rate`, `dd`→`max_drawdown`). */
export function normalizeBaseline(
  baseline: Record<string, any> | null | undefined,
): {
  trades: number;
  pnl: number;
  sharpe: number;
  win_rate: number;
  max_drawdown: number;
  alpha: number;
  source: string;
} | null {
  if (!baseline) return null;
  return {
    trades: baseline.trades ?? 0,
    pnl: baseline.pnl ?? 0,
    sharpe: baseline.sharpe ?? 0,
    win_rate: baseline.win_rate ?? baseline.wr ?? 0,
    max_drawdown: baseline.max_drawdown ?? baseline.dd ?? 0,
    alpha: baseline.alpha ?? 0,
    source: baseline.baseline_source ?? baseline.source ?? 'backtest OOS',
  };
}

/** OPT-002 — normalise `top5` → `top_trials` et `final_score` → `final`. */
export function normalizeTopTrials(result: OptimizeJob['result']): OptimizeTrial[] {
  if (!result) return [];
  const trials = result.top_trials ?? result.top5 ?? [];
  return trials.map((t) => ({
    ...t,
    final: t.final ?? t.final_score,
  }));
}

/** BT-006 — equity finale (alias backend `final_equity`). */
export function equityFinal(r: BacktestResult): number | null {
  return r.equity_final ?? r.final_equity ?? null;
}

/** BT-006 — buy & hold (PnL ou %). */
export function buyHold(r: BacktestResult): { pnl: number | null; pct: number | null } {
  return {
    pnl: r.buy_and_hold_pnl ?? null,
    pct: r.buy_and_hold_pct ?? null,
  };
}
