/**
 * Types correspondant aux réponses de l'API FastAPI du backend.
 * Source : app/api/routes/* du backend.
 */

export interface BotStatus {
  status: 'running' | 'idle' | 'stopped' | 'not_started';
  paper_mode: boolean;
  timeframe: string;
  timeframes: string[];
  strategies: string[];
  capital?: number;
  cycle?: number;
  total_pnl?: number;
  total_pnl_pct?: number;
  total_trades?: number;
  win_rate?: number;
  profit_factor?: number;
  total_fees?: number;
  best_trade?: number;
  positions?: Position[];
  by_strategy?: Record<string, StrategyStats>;
  signal_log?: SignalLogEntry[];
  active_per_tf?: Record<string, string[]>;
  circuit_breaker_active?: boolean;
  circuit_breaker_reason?: string;
  daily_pnl_pct?: number;
  global_dd_pct?: number;
  current_risk?: number;
  daily_dd_limit?: number;
  global_dd_limit?: number;
  capital_allocation?: SlotBudget[];
  circuit_breakers?: CircuitBreakerStatus[];
  slot_states?: SlotState[];
  volatility_brake?: boolean;
  margin_enabled?: boolean;
  margin_level?: number | null;
  margin_interest?: number;
  margin_mode?: string | null;
  balance_detail?: BalanceDetail | null;
  bots?: BotIdentity[];
  lifecycle?: LifecycleSnapshot | null;
  shadow_allocation?: Record<string, any>;
  last_scan_time?: string | null;
  last_symbols_scanned?: string[];
}

export interface Position {
  id: string;
  symbol: string;
  side: 'long' | 'short';
  strategy: string;
  timeframe: string;
  score: number;
  entry: number;
  stop: number;
  size: number;
  notional: number;
  fees: number;
  upnl: number;
  open_time: number;
  reason: string;
}

/**
 * Contexte d'exécution facturé par un backtest / une optimisation (S11).
 * Produit par `app/core/execution.py::cost_model` — les valeurs sont celles
 * RÉELLEMENT appliquées : `borrow_rate_daily` vaut 0 sur une venue spot, et
 * `fee_rate_*` reflète le `fee_pct` de la venue quand elle en déclare un.
 */
export interface CostModel {
  venue: string;
  market_type: string;
  margin_mode?: string | null;
  max_leverage: number;
  asset_class: string;
  quote_currency: string;
  exchange?: string | null;
  calendar?: string;
  can_execute?: boolean;
  allow_short?: boolean;
  fee_rate_taker: number;
  fee_rate_maker: number;
  fee_pct_override?: number | null;
  fee_fixed: number;
  fee_min: number;
  transaction_tax_pct: number;
  tax_on_buy_only: boolean;
  borrows: boolean;
  borrow_rate_daily: number;
  borrow_periods_per_day: number;
  borrow_rate_annual: number;
  spread_pct: number;
  slippage_model: string;
  slippage_k: number;
  partial_fill_pct: number;
  fractional: boolean;
  lot_size: number;
  tick_size: number;
  min_notional: number;
  max_notional_pct: number;
}

export interface StrategyStats {
  trades: number;
  wins: number;
  pnl: number;
  fees: number;
  win_rate: number;
  total_pnl: number;
  total_fees: number;
  total_trades: number;
  profit_factor: number;
  sharpe: number;
  max_drawdown: number;
}

export interface SignalLogEntry {
  time: string;
  symbol: string;
  strategy: string;
  side: string;
  score: number;
  threshold: number;
  timeframe: string;
  status: 'opened' | 'rejected' | 'closed';
  entry?: number;
  exit?: number;
  pnl?: number;
  pnl_pct?: number;
  reason: string;
}

export interface SlotBudget {
  slot_key: string;
  strategy: string;
  tf: string;
  symbol?: string;
  enabled: boolean;
  budget_pct: number;
  budget_usdc: number;
  used_notional: number;
  used_pct: number;
  weekly_pnl: number;
  weekly_trades: number;
  weekly_wins: number;
  next_rebalance: string;
  paused: boolean;
  pause_reason: string;
  consecutive_losses: number;
  win_rate_15t: number;
  daily_pnl: number;
  excluded_by_optimizer?: boolean;
}

// ── S12 : enveloppes de risque emboîtées venue → symbole → slot ──────────
//
// Règle d'affichage que ces types servent : un montant de risque va TOUJOURS
// avec sa base. Un risque en devise sans son enveloppe n'est pas
// interprétable — c'est ce qui a laissé passer la divergence backtest
// 1 000 € / live 90 € sans que rien ne la signale.

export interface RiskVenue {
  venue: string;
  currency: string;
  envelope: number;
  notional_engaged: number;
  risk_budget: number;
  risk_engaged: number;
  risk_pct_used: number;
}

export interface RiskSymbol {
  venue: string;
  symbol: string;
  currency: string;
  envelope: number;
  notional_engaged: number;
  risk_budget: number;
  risk_engaged: number;
}

export interface RiskSlot {
  slot_key: string;
  venue: string;
  symbol: string;
  currency: string;
  /** Poids par confiance d'edge — c'est l'allocation, pas un budget à part. */
  weight: number;
  envelope: number;
  max_notional: number;
  risk_amount: number;
  risk_engaged: number;
  notional_engaged: number;
  edge_ci_low: number | null;
}

export interface RiskRejections {
  total?: number;
  par_motif?: Record<string, number>;
  par_slot?: Record<string, number>;
  par_symbole?: Record<string, number>;
}

export interface RiskOverview {
  venues: RiskVenue[];
  symbols: RiskSymbol[];
  slots: RiskSlot[];
  total_risk_engaged: number;
  rejections: RiskRejections;
}

export interface RiskDiagnostic {
  severity: 'error' | 'warning';
  code: string;
  /** `venue:<nom>` | `symbol:<sym>` | `slot:<clé>` */
  scope: string;
  message: string;
  values: Record<string, number | string>;
}

export interface RiskDiagnostics {
  diagnostics: RiskDiagnostic[];
  errors: number;
  warnings: number;
}

export interface VenueEnvelopeConfig {
  capital?: number;
  max_symbol_exposure_pct?: number;
  symbol_risk_pct?: number;
  venue_risk_pct?: number;
}

/** Base économique ayant mesuré une edge (§5.2). */
export interface EdgeBase {
  venue: string;
  slot_envelope: number;
  trade_risk_pct: number;
  currency: string;
  as_of: string;
}

export interface CircuitBreakerStatus {
  slot_key: string;
  paused: boolean;
  pause_reason: string;
  consecutive_losses: number;
  win_rate_15t: number;
  daily_pnl: number;
}

export interface SlotState {
  slot_key: string;
  consecutive_losses: number;
  last_trades: boolean[];
  daily_pnl: number;
  daily_trades: number;
  day_key: string;
  paused_until: number;
  pause_reason: string;
  win_rate: number;
}

export interface BalanceDetail {
  free: number;
  used: number;
  total: number;
  borrowed: number;
}

export interface BotIdentity {
  slot_key: string;
  strategy: string;
  timeframe: string;
  symbol: string;
  generation: number;
  born_at: string;
  parent_slot?: string;
  lineage: string[];
}

export interface LifecycleSnapshot {
  states: Record<string, 'candidat' | 'essai' | 'actif' | 'retire'>;
  counts: Record<string, number>;
  reopt_queue: string[];
}

export interface Trade {
  id: number;
  time: string;
  symbol: string;
  side: 'long' | 'short';
  strategy: string;
  timeframe?: string;  // UI-04 : ajouté pour le filtre slot 3-parties
  entry: number;
  exit: number;
  pnl: number;
  pnl_pct: number;
  fees: number;
  status: string;
  score: number;
  reason: string;
}

export interface Bot {
  slot_key: string;
  strategy: string;
  timeframe: string;
  symbol: string;
  state: 'candidat' | 'essai' | 'actif' | 'retire';
  identity?: BotIdentity;
  budget?: SlotBudget;
  sim?: any;
  monte_carlo?: any;
  edge?: {
    available: boolean;
    ci_low_pct?: number;
    ci_high_pct?: number;
    n?: number;
    worst_trade_pct?: number;
  };
  edge_significant: boolean;
  /** Slot forcé ACTIF via `lifecycle.force_active` (D6). */
  force_active?: boolean;
  /** @deprecated Ancien nom de `force_active` — encore émis par l'API. */
  manual_active?: boolean;
  live?: any;
  contract?: {
    verdict?: string;
    in_band?: boolean;
  };
  verdict?: string;
  in_band?: boolean;
  run_date?: string;
}

// ── WebSocket events ───────────────────────────────────────────────────────

export interface WSEvent<T = any> {
  type: string;
  ts: string;
  data: T;
}

export interface TradeOpenedData {
  slot_key: string;
  symbol: string;
  side: 'long' | 'short';
  size: number;
  entry: number;
  stop: number;
  strategy: string;
  timeframe: string;
  score: number;
  reason: string;
}

export interface TradeClosedData {
  slot_key: string;
  symbol: string;
  side: 'long' | 'short';
  entry: number;
  exit: number;
  pnl: number;
  pnl_pct: number;
  fees: number;
  reason: string;
  duration_bars: number;
}

export interface SignalData {
  slot_key: string;
  symbol: string;
  timeframe: string;
  side: string;
  score: number;
  accepted: boolean;
  reason: string;
}

export interface RiskData {
  severity: 'info' | 'warning' | 'critical';
  [key: string]: any;
}

export interface CycleUpdateData {
  cycle: number;
  capital: number;
  open_positions: number;
  scan_duration_ms: number;
}

export interface TickerData {
  symbol: string;
  price: number;
  bid?: number;
  ask?: number;
  change_pct?: number;
}

// ── Backtest ───────────────────────────────────────────────────────────────

export interface BacktestResult {
  strategy: string;
  symbol: string;
  timeframe: string;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  total_fees: number;
  sharpe: number;
  expectancy: number;
  max_drawdown: number;
  profit_factor: number;
  best_trade: number;
  worst_trade: number;
  trades: BacktestTrade[];
  equity_curve: { time: string; equity: number }[];
  diagnostics?: Record<string, any>;
}

export interface BacktestTrade {
  time: string;
  symbol: string;
  side: 'long' | 'short';
  strategy: string;
  entry: number;
  exit: number;
  pnl: number;
  pnl_pct: number;
  fees: number;
  reason: string;
}

// ── Optimizer ───────────────────────────────────────────────────────────────

export interface OptimizeJob {
  job_id: string;
  strategy: string;
  timeframe: string;
  symbol?: string;
  status: 'pending' | 'running' | 'done' | 'error' | 'cancelled';
  progress: number;
  best_score: number;
  trials_done: number;
  n_trials: number;
  method: string;
  started_at?: number;
  finished_at?: number;
  baseline?: Record<string, any>;
  trials?: any[];
  result?: {
    best_params?: Record<string, any>;
    best_oos_score?: number;
    best_oos_pnl?: number;
    best_oos_trades?: number;
    best_oos_wr?: number;
    best_oos_sharpe?: number;
    /** Contexte facturé pendant toute l'optimisation (S11) — sans lui, deux
     *  `oos_score` ne sont pas comparables. */
    cost_model?: CostModel;
  };
  applied?: boolean;
  error?: string;
}

export interface OptimizeSpaces {
  [strategy: string]: {
    params: Record<string, any>;
    timeframes: string[];
    n_combos: number;
    is_ml: boolean;
  };
}

export interface OptimizeResults {
  by_strategy_tf: Record<string, Record<string, any>>;
  active_per_tf: Record<string, string[]>;
}

// ── ML ──────────────────────────────────────────────────────────────────────

export interface MLStrategyInfo {
  is_trained: boolean;
  best_auc: number;
  next_retrain_at: number | null;
}

// ── ML Model Registry (ML-02) ───────────────────────────────────────────────

export interface ModelFeatureImportance {
  feature: string;
  gain: number;
}

export interface ModelRegimeAuc {
  n: number | null;
  auc: number | null;
  approx_n?: number;
}

export interface ModelRegimeFeatureImportance {
  n: number;
  top: { feature: string; contrib: number }[];
}

export interface ModelRegimeSimilarity {
  /** Spearman sur le vecteur complet d'importances : ≈1 = mêmes priorités. */
  spearman?: number | null;
  /** Part de features communes dans le top-N (0..1). */
  top_overlap: number;
}

export interface ModelTrainMeta {
  n_features?: number;
  n_train?: number;
  n_valid?: number;
  horizons?: number[];
  calibrated?: boolean;
  cal_err?: { amp?: number; dir?: number };
  auc_dir_by_regime?: Record<string, ModelRegimeAuc>;
  feature_importance_amp?: ModelFeatureImportance[];
  feature_importance_dir?: ModelFeatureImportance[];
  feature_importance_dir_by_regime?: Record<string, ModelRegimeFeatureImportance>;
  regime_feature_similarity?: Record<string, ModelRegimeSimilarity>;
  [key: string]: unknown;
}

export interface ModelArtifact {
  path_prefix: string;
  /** Symbole d'ENTRAÎNEMENT (provenance) — le registre ne range pas par
   *  symbole, l'artefact sert tous les symboles tradés. */
  train_symbol: string | null;
  tf: string;
  recipe: string;
  version_id: string;
  train_start: string | null;
  train_end: string | null;
  n_bars: number | null;
  auc: number;
  recipe_hash: string | null;
  git_commit: string | null;
  source: string | null;
  created_at: string | null;
  gate_decision: string | null;
  train_meta?: ModelTrainMeta;
}

export interface ModelRegistryEntry {
  tf: string;
  recipe: string;
  /** Provenance de la version active — affichage seul, pas une clé. */
  train_symbol: string | null;
  n_versions: number;
  active: ModelArtifact | null;
  pinned_version_id: string | null;
  freshness_warning: string | null;
}

export interface ModelDecision {
  ts: string;
  version_id: string;
  decision: string;
  source?: string;
  reason?: string;
  previous_decision?: string;
  [key: string]: unknown;
}

export interface MLJobStatus {
  kind: 'train' | 'sweep';
  status: 'running' | 'done' | 'error';
  strategy: string;
  symbol: string;
  tf: string;
  started_at: number;
  finished_at?: number;
  result: Record<string, any> | null;
  error: string | null;
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
