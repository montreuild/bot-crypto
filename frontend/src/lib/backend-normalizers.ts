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
  MlInfoEntry,
  MlInfoPayload,
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
export interface NormalizedMlInfo {
  auc: number | null;
  n_features: number | null;
  lookahead: number | null;
  proba_up: number | null;
  model_version: string | null;
  /** Fenêtre d'entraînement du modèle figé effectivement chargé. */
  train_start: string | null;
  train_end: string | null;
  /**
   * Le backtest a détecté que la fenêtre d'entraînement du modèle recouvre la
   * fenêtre évaluée — fuite temporelle. Calculé côté serveur par
   * `_resolve_frozen_ml_model` via `model_registry.overlaps()`, sur l'artefact
   * RÉELLEMENT chargé (et non sur la version active du moment).
   */
  overlap_warning: boolean;
  /** Aucun modèle résoluble : la stratégie s'est entraînée en ligne. */
  fallback_to_inline: boolean;
}

function _fromEntry(entry: MlInfoEntry): NormalizedMlInfo {
  return {
    auc: entry.auc ?? null,
    n_features: entry.n_features ?? entry.nFeatures ?? null,
    lookahead: entry.lookahead ?? null,
    proba_up: entry.proba_up ?? entry.probaUp ?? null,
    model_version: entry.version_id ?? entry.model_version ?? null,
    train_start: entry.train_start ?? null,
    train_end: entry.train_end ?? null,
    overlap_warning: Boolean(entry.overlap_warning),
    fallback_to_inline: Boolean(entry.fallback_to_inline),
  };
}

export function normalizeMlInfo(
  mlInfo: unknown,
  strategy: string,
): NormalizedMlInfo | null {
  if (!mlInfo || typeof mlInfo !== 'object') return null;

  if (Array.isArray(mlInfo)) {
    const list = mlInfo as MlInfoEntry[];
    const entry = list.find((m) => m?.strategy === strategy) ?? list[0];
    return entry ? _fromEntry(entry) : null;
  }

  const payload = mlInfo as MlInfoPayload;
  if (payload.models && typeof payload.models === 'object') {
    const models = payload.models;
    const entry = models[strategy] ?? Object.values(models)[0];
    return entry ? _fromEntry(entry) : null;
  }

  // Objet déjà aplati (déjà normalisé en amont, ou payload legacy).
  return _fromEntry(payload as MlInfoEntry);
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
export function equityFinal(r: Pick<BacktestResult, 'equity_final' | 'final_equity'>): number | null {
  return r.equity_final ?? r.final_equity ?? null;
}

/** `equity_curve` backend = tableau de nombres, pas `{equity}` — `.map(p => p.equity)` donnait 0$. */
export function equityValues(curve: unknown): number[] {
  if (!Array.isArray(curve) || curve.length === 0) return [];
  const out: number[] = [];
  for (const p of curve) {
    const n = typeof p === 'number' ? p
      : (p && typeof p === 'object' && 'equity' in p ? Number((p as { equity: unknown }).equity) : NaN);
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
}

/** Max DD backend déjà en % (ex. -5.10). Ne plus ×100 (sinon -8791 %). */
export function formatDrawdownPct(v: number | null | undefined): string {
  const p = asPercentPoints(v);
  return p == null ? '—' : `${p.toFixed(1)}%`;
}

/**
 * WR / DD / proba : le backend mélange ratio 0–1 et points 0–100.
 * |v| ≤ 1 → ×100 ; au-delà on ne touche pas (évite 25.8 → 2580 %).
 */
export function asPercentPoints(v: number | null | undefined): number | null {
  if (v == null || !Number.isFinite(Number(v))) return null;
  const n = Number(v);
  if (n === 0) return 0;
  return Math.abs(n) <= 1 ? n * 100 : n;
}

export function formatPctPoints(v: number | null | undefined, digits = 1): string {
  const p = asPercentPoints(v);
  return p == null ? '—' : `${p.toFixed(digits)}%`;
}

/** Alpha backend = PnL − B&H en devise. Afficher en % du capital, pas `-6420%`. */
export function alphaPct(
  alpha: number | null | undefined,
  initialCapital?: number | null,
  alphaVsBh?: number | null,
): number | null {
  if (alphaVsBh != null && Number.isFinite(alphaVsBh) && Math.abs(alphaVsBh) < 500) {
    return alphaVsBh;
  }
  if (alpha == null || !Number.isFinite(alpha)) return null;
  if (initialCapital && initialCapital > 0 && Math.abs(alpha) > 5) {
    return (alpha / initialCapital) * 100;
  }
  return alpha;
}

/** BT-006 — buy & hold (PnL ou %). */
export function buyHold(r: Pick<BacktestResult, 'buy_and_hold_pnl' | 'buy_and_hold_pct'>): { pnl: number | null; pct: number | null } {
  return {
    pnl: r.buy_and_hold_pnl ?? null,
    pct: r.buy_and_hold_pct ?? null,
  };
}
