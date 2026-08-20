/**
 * Contrats UI + reexport des contrats serveur (FE-03).
 * Serveur : types/generated.ts (python scripts/gen_frontend_types.py).
 * UI-only (WS, SMC) : types/ui.ts.
 * Vues riches (backtest / optimize / ML) : types/views.ts.
 * Schema brut : python scripts/export_openapi.py -> types/openapi.json.
 */

import type {
  BotIdentity,
  LifecycleSnapshot,
  SlotBudget,
  StrategyStats,
} from './generated';

export type * from './generated';
export type * from './ui';
export type * from './views';

/** Base économique ayant mesuré une edge (§5.2). */
export interface EdgeBase {
  venue: string;
  slot_envelope: number;
  trade_risk_pct: number;
  currency: string;
  as_of: string;
}

// ── Data / Candles ──────────────────────────────────────────────────────────

export interface CandlesDataset {
  symbol: string;
  timeframe: string;
  count: number;
  first: string;
  last: string;
  size_bytes?: number;
}

// ── Derivatives ─────────────────────────────────────────────────────────────

export interface DerivativesData {
  symbol: string;
  period: string;
  metrics: {
    funding_rate?: TimeSeries;
    open_interest?: TimeSeries;
    long_short_ratio?: TimeSeries;
    taker_buy_sell_ratio?: TimeSeries;
  };
  price?: {
    time: number[];
    close: number[];
  };
}

export interface TimeSeries {
  time: number[];
  value: (number | null)[];
  count: number;
  first: string;
  last: string;
}

// ── Replay ──────────────────────────────────────────────────────────────────

export interface ReplayResult {
  symbol: string;
  months: number;
  timeframes_tested: string[];
  strategies_tested: string[];
  by_timeframe: Record<string, {
    n_bars: number;
    date_from: string;
    date_to: string;
    days_covered: number;
    ohlcv?: { time: number[]; close: number[]; open: number[]; high: number[]; low: number[] };
    by_strategy: Record<string, any>;
    gaps_warning?: string | null;
  }>;
  cross_tf_summary: Array<{
    tf: string;
    strategy: string;
    n_bars: number;
    days_covered: number;
    trades: number;
    win_rate: number;
    pnl: number;
    pnl_pct: number;
    sharpe: number;
    max_drawdown: number;
    profit_factor: number;
    final_equity: number;
  }>;
}

// ── Audit ───────────────────────────────────────────────────────────────────

export interface AuditResult {
  results: Array<{
    strategy: string;
    tf: string;
    symbol: string;
    slot_key: string;
    run_date: string;
    oos_score: number;
    params: Record<string, any>;
  }>;
  total: number;
  backtests: Record<string, any>;
}

// ── Notifications ───────────────────────────────────────────────────────────

export interface Notification {
  ts: string;
  level: 'info' | 'warning' | 'critical';
  message: string;
  title?: string;
}

// ── P1-4 : validation d'un paramétrage optimisé ─────────────────────────────
// `POST /api/optimize/validate` rejoue les `best_params` d'un job terminé.

/** Tenue de la STRATÉGIE sur les segments d'un régime donné. */
export interface RegimeStrategyPerf {
  n_trades: number;
  pnl: number;
  /** Gain de la stratégie en % du capital, par régime. */
  pnl_pct?: number;
  win_rate: number;
  avg_pnl: number;
  best: number;
  worst: number;
}

/** Comportement du MARCHÉ sur les segments d'un régime — contexte, pas mesure. */
export interface RegimeMarketSummary {
  n_segments: number;
  total_bars: number;
  avg_return_pct: number;
  best_return_pct: number;
  worst_return_pct: number;
  avg_sharpe: number;
  worst_max_dd: number;
}

export interface OptimizeValidateResult {
  method: 'monte_carlo' | 'regime';
  n_trades: number;
  /** method="monte_carlo" — sortie de `MonteCarlo.run`. */
  result?: Record<string, number>;
  /** method="regime" — segments détectés sur la série de prix. */
  segments?: Array<{
    regime: string;
    start_time: string;
    end_time: string;
    n_bars: number;
    metrics: Record<string, number>;
  }>;
  /** method="regime" — contexte marché par régime. */
  market?: Record<string, RegimeMarketSummary>;
  /**
   * method="regime" — performance de la stratégie par régime. C'est la seule
   * partie qui dépend des `best_params` : `market` et `segments` seraient
   * identiques pour n'importe quel paramétrage.
   */
  by_strategy?: Record<string, RegimeStrategyPerf>;
}

// ── Contrats restants U-05 ─────────────────────────────────────────────────

export interface OosSlot {
  slot_key?: string;
  monte_carlo?: {
    runs?: number;
    return_p5_pct?: number;
    return_mean_pct?: number;
    return_p95_pct?: number;
    max_dd_p95_pct?: number;
    prob_profit?: number;
  };
  live?: {
    n_trades?: number;
    avg_return_pct?: number | null;
  };
  contract?: {
    available?: boolean;
    live_mean_pct?: number | null;
    in_band?: boolean | null;
    verdict?: string;
  };
  [key: string]: unknown;
}

export interface OosTracker {
  slots?: Record<string, OosSlot> | OosSlot[];
}

export interface ForwardTestResult {
  ran?: boolean;
  edge?: {
    available?: boolean;
    ci_low_pct?: number | null;
    ci_high_pct?: number | null;
  };
  sim?: {
    total_trades?: number;
    return_pct?: number;
    win_rate?: number;
  };
  contract?: { verdict?: string };
}

export interface OptimizeStartResult {
  n_jobs_created?: number;
  job_ids?: string[];
  received_bars?: Record<string, number>;
  fetch_details?: Record<string, number>;
  skipped?: unknown[];
}

export interface AuditEvent {
  id: string | number;
  ts: string;
  action: string;
  actor?: string;
  ip?: string;
  method?: string;
  path?: string;
  status_code?: number;
  details?: unknown;
}

export interface CandleStoreEntry {
  count?: number;
  first?: string;
  last?: string;
  size_bytes?: number;
}

export type CandleStore = Record<string, Record<string, CandleStoreEntry>>;

export interface VenueDef {
  name?: string;
  market_type?: string;
  asset_class?: string;
  quote_currency?: string;
  can_execute?: boolean;
  provider?: string;
  data_provider?: string;
  exchange?: string;
  max_leverage?: number;
  margin_mode?: string;
  allow_short?: boolean;
}

export interface AppConfig {
  all_strategies?: string[];
  scanner?: { symbols?: string[] };
  strategy_params?: Record<string, Record<string, unknown>>;
  trading?: {
    timeframes?: string[];
    max_drawdown_global?: number;
    daily_drawdown_limit?: number;
    paper_mode?: boolean;
    max_leverage?: number;
  };
  risk?: {
    envelopes?: Record<string, { capital?: number }>;
    profile?: string;
    profiles?: Record<string, number>;
  };
  venues?: {
    default?: string;
    defs?: Record<string, VenueDef>;
  };
  notifications?: Record<string, boolean>;
  exchange?: { api_key?: string; name?: string; margin?: boolean };
  providers?: {
    yfinance?: { suffix?: string; min_request_interval?: number; cache_ttl?: number };
  };
}

export interface PortfolioSnapshot {
  by_strategy?: Record<string, StrategyStats>;
  total_pnl?: number;
  lifecycle?: LifecycleSnapshot;
  risk?: { kill_switch?: boolean };
}

export interface BacktestSettings {
  strategies?: string[];
  all_strategies?: string[];
  strategies_enabled?: string[];
  score_threshold?: number;
}

export interface RiskPresetRemote {
  name?: string;
  current?: string;
  [key: string]: unknown;
}

export interface NotificationItem {
  id?: string | number;
  level?: string;
  title?: string;
  message?: string;
  ts?: string;
  [key: string]: unknown;
}

export interface UniverseInfo {
  id?: string;
  label?: string;
  count?: number;
  venue?: string;
  asset_class?: string;
  quote_currency?: string;
  n_symbols?: number;
  verified?: boolean;
}

export interface UniverseMember {
  symbol?: string;
  name?: string;
}

export interface FeesBreakdown {
  taker?: number | null;
  maker?: number | null;
  borrow?: number | null;
  stop?: number | null;
}

export interface DailyStat {
  date?: string;
  pnl?: number | null;
  equity?: number | null;
}

export interface MlRecipe {
  recipe: string;
  trainable?: boolean;
  reason?: string | null;
  features_catalog?: string | null;
  label_scheme?: string | null;
  heads?: string[];
}

export interface MlInfoEntry {
  strategy?: string;
  auc?: number;
  n_features?: number;
  nFeatures?: number;
  lookahead?: number;
  proba_up?: number;
  probaUp?: number;
  version_id?: string;
  model_version?: string;
  train_start?: string;
  train_end?: string;
  overlap_warning?: boolean;
  fallback_to_inline?: boolean;
}

export interface MlInfoPayload {
  models?: Record<string, MlInfoEntry>;
  [key: string]: unknown;
}

export interface DerivativesStatus {
  enabled?: boolean;
}

export interface DerivativesPayload {
  enabled?: boolean;
  metrics?: Record<string, unknown>;
  price?: unknown;
}

