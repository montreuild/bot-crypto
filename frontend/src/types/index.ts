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
  diagnostics?: BacktestDiagnostics | Record<string, any>;
  /** BT-010 — panneau ML. Présent seulement pour les stratégies `ml_*`. */
  ml_info?: BacktestMLInfo | Record<string, any>;
  /** BT-006 — KPIs par stratégie. */
  final_equity?: number;
  /** Alias backend : `final_equity`. */
  equity_final?: number;
  buy_and_hold_pnl?: number;
  buy_and_hold_pct?: number;
  alpha?: number;
  /** Walk-Forward, Monte-Carlo, par stratégie. */
  walk_forward?: any;
  monte_carlo?: any;
  /** OHLCV brut pour le chart prix+signaux (BT-001). */
  ohlcv?: {
    time: (string | number)[];
    open: number[];
    high: number[];
    low: number[];
    close: number[];
    volume?: number[];
  };
  /** Métadonnées pour le diagnostic d'intégrité des données. */
  date_from?: string;
  date_to?: string;
  // ── QW-2 : bornes de plage réellement appliquées ─────────────────────────
  // Vides si le run s'est fait en mode « dernières N bougies ». À comparer
  // avec `date_from`/`date_to`, qui décrivent les données effectivement
  // trouvées : un écart signale un cache plus court que la plage demandée.
  requested_start_date?: string;
  requested_end_date?: string;
  /** QW-3 — un fetch réseau a été forcé avant ce run (données à jour). */
  refreshed?: boolean;
  gaps_warning?: string;
  cost_model?: CostModel;
  /** Étude vs réel (dual_pass). */
  runs?: any;
  // ── QW-1 : métriques étendues (S3-07 — branchement a posteriori) ──────────
  /** Sortino ratio annualisé (volatilité downside uniquement). */
  sortino?: number;
  /** Calmar ratio (CAGR / |Max DD|). */
  calmar?: number;
  /** CAGR en % (Compound Annual Growth Rate). */
  cagr?: number;
  /** Alpha annualisé vs Buy & Hold en %. */
  alpha_vs_bh?: number;
  // ── QW-3 : agrégats de coûts pour analyse what-if frais/levier ────────────
  /** Coût total d'emprunt margin sur la période. */
  total_borrow_cost?: number;
  /** Coût total de slippage (spread + impact), isolé depuis L0. */
  total_slippage_cost?: number;
  // ── L0/L2 : décomposition brut → net ──────────────────────────────────────
  /** Funding payé (ou encaissé, si négatif) sur les venues perpétuelles. */
  total_funding_cost?: number;
  /** Frais d'entrée agrégés — l'écart exact entre `total_pnl` et `net_profit`. */
  total_entry_fees?: number;
  /** PnL directionnel avant tout coût. */
  gross_profit?: number;
  /**
   * Variation d'équité réelle (`final_equity − initial_capital`).
   * ⚠ Distinct de `total_pnl`, qui somme les PnL de clôture sans retrancher
   * les frais d'entrée (prélevés à l'ouverture). L'écart vaut exactement
   * `total_entry_fees` — cf. docs/MESURE_GEOMETRIE_SORTIE.md §3.
   */
  net_profit?: number;
  // ── Axes d'analyse SMC (L0–L6) ────────────────────────────────────────────
  by_exit_reason?: Record<string, StrategyStats>;
  /** §5 — PnL par JAMBE de sortie (tp1, tp2, runner). `by_exit_reason` compte
   *  des trades ; ceci dit d'où vient l'argent dans un trade fractionné. */
  by_exit_leg?: Record<string, { n: number; pnl: number; win_rate: number; part_pct: number }>;
  /** Mode de sortie appliqué — deux runs qui ne sortent pas de la même façon
   *  ne sont pas comparables de bonne foi. */
  exit_mode?: string;
  by_structure_state?: Record<string, StrategyStats>;
  by_sequence_type?: Record<string, StrategyStats>;
  by_tier?: Record<string, StrategyStats>;
  by_target_class?: Record<string, StrategyStats>;
  // ── QW-5 : recommandations d'amélioration post-backtest ───────────────────
  /** Liste ordonnée de recommandations (sévérité critical > warning > info > positive). */
  recommendations?: BacktestRecommendation[];
  /** Synthèse des recommandations (compteurs + verdict global). */
  recommendations_summary?: BacktestRecommendationsSummary;
  // ── QW-6 : mode realistic_risk (circuit breakers en backtest) ──────────────
  /** True si le backtest a été lancé avec realistic_risk=True. */
  realistic_risk?: boolean;
  /** Diagnostics du risk gate (circuit breakers déclenchés, slots pausés, etc.). */
  /**
   * QW-6 — diagnostics des circuit breakers. Renseigné PAR STRATÉGIE (chaque
   * stratégie a son propre `Backtester`, donc son propre gate), jamais à la
   * racine du résultat.
   */
  realistic_risk_diagnostics?: {
    halted: boolean;
    halt_reason: string;
    /** "daily_dd" = levé le lendemain ; "global_dd" = arrêt définitif. */
    halt_kind: '' | 'daily_dd' | 'global_dd';
    volatility_brake_active: boolean;
    /** Bougies pendant lesquelles le frein a mordu — mesure son effet réel. */
    volatility_brake_bars: number;
    n_slots_paused: number;
    slots: Record<string, {
      consecutive_losses: number;
      daily_pnl: number;
      daily_trades: number;
      paused: boolean;
      pause_reason: string;
      pause_kind: '' | 'consec_loss' | 'daily_dd';
    }>;
  };
}

export interface BacktestRecommendation {
  /** 'critical' | 'warning' | 'info' | 'positive'. */
  severity: 'critical' | 'warning' | 'info' | 'positive';
  /** Code machine (ex: 'OUTLIER_CONCENTRATION', 'EXCELLENT_SHARPE'). */
  code: string;
  /** Titre court (ex: 'Concentration sur 3 trades'). */
  title: string;
  /** Explication détaillée. */
  message: string;
  /** Action recommandée (ex: 'Réduire risk_per_trade'). */
  action: string;
  /** Lien d'action optionnel (ex: '/settings?tab=risk'). */
  action_link?: string;
  /** Chiffres spécifiques à la reco (variable selon le code). */
  metrics?: Record<string, any>;
}

export interface BacktestRecommendationsSummary {
  /** Compteurs par sévérité. */
  counts: {
    critical: number;
    warning: number;
    info: number;
    positive: number;
  };
  /** Verdict global : 'critical' | 'risky' | 'neutral' | 'positive'. */
  verdict: 'critical' | 'risky' | 'neutral' | 'positive';
  /** Libellé humain du verdict. */
  verdict_label: string;
  /** Nombre total de recommandations. */
  total: number;
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
  /** Alias backend : `reason` est le nom backend de `signal_reason`. */
  signal_reason?: string;
  /** BT-001 — markers + stop lines. */
  id?: number | string;
  bar?: number;
  exit_bar?: number;
  setup?: string;
  setup_v7?: string;
  exit_reason?: string;
  score?: number;
  duration_bars?: number;
  sl_atr_mult?: number;
  tp_atr_mult?: number;
  exit_after_bars?: number;
  size_factor?: number;
  regime_lbl?: string;
  bearish_excess?: number;
  size?: number;
  notional?: number;
  stop?: number;
  stop_initial?: number;
  /** Array de {bar, stop} — évolution du trailing. */
  stop_trail?: Array<{ bar: number; stop: number }>;
  conditions?: Array<{ label: string; passed: boolean }>;
  indicators?: Record<string, number>;
  disable_trailing?: boolean;
  status?: string;
  entry_time?: string;
  exit_time?: string;
  /** §65 — module SMC/ICT du setup (SMC Core, ICT Session…). */
  module?: string;
  /** L3 (§60) — état de structure séquentiel à l'entrée. */
  structure_state?: string;
  /** L6 (§72) — CONTINUATION / REVERSAL / EARLY_REVERSAL / FAILED_REVERSAL. */
  sequence_type?: string;
  sequence_id?: string;
  /** L6 (§97) — tous les signaux d'un même événement de liquidité le partagent. */
  market_event_id?: string;
  /** L6 (§71) — A / B / C / D. */
  tier?: string;
  /** L0 (§99) — contexte de décision. */
  htf_bias?: string;
  session?: string;
  gross_rr?: number;
  net_rr?: number;
  score_breakdown?: Record<string, number>;
  planned_stop?: number;
  planned_tp?: number;
  /** L0 — coûts ventilés. `fees` reste l'agrégat. */
  entry_fees?: number;
  slippage_cost?: number;
  funding_cost?: number;
  gross_pnl?: number;
  /** L1 (§29) — jambes sorties avant la clôture finale. Vide en tout-ou-rien. */
  exits?: Array<{
    bar: number;
    price: number;
    size: number;
    fraction: number;
    reason: string;
    pnl: number;
  }>;
  size_initial?: number;
  mfe?: number;
  mae?: number;
  /** Pour les backtests multi-stratégies — la stratégie du trade. */
  strategy_name?: string;
}

/** BT-003 — panneau Diagnostics. */
export interface BacktestDiagnostics {
  bars_total?: number;
  bars_in_position?: number;
  signals_accepted?: number;
  /** Alias backend : `signal_accepted` (singulier). */
  signal_accepted?: number;
  trades_opened?: number;
  rejections?: {
    notional?: number;
    atr_le_zero?: number;
  };
  /** Alias backend : clés plates `rejected_notional` / `rejected_atr_zero`. */
  rejected_notional?: number;
  rejected_atr_zero?: number;
  max_bars_in_position?: number;
  max_bars_without_signal?: number;
  /** Alias backend : `max_bars_no_signal`. */
  max_bars_no_signal?: number;
  per_strategy?: Array<{
    strategy: string;
    evaluated?: number;
    none?: number;
    proposed?: number;
    below_threshold?: number;
    above_threshold?: number;
    errors?: number;
  }>;
}

/** BT-010 — panneau ML spécifique aux stratégies `ml_*`. */
export interface BacktestMLInfo {
  auc?: number;
  n_features?: number;
  lookahead?: number;
  proba_up?: number;
  model_version?: string;
  last_train?: string;
  next_retrain?: string;
}

// ── Optimizer ───────────────────────────────────────────────────────────────

export interface OptimizeJob {
  job_id: string;
  strategy: string;
  timeframe: string;
  symbol?: string;
  status: 'pending' | 'running' | 'done' | 'error' | 'cancelled' | 'queued' | 'skipped';
  progress: number;
  best_score: number;
  trials_done: number;
  n_trials: number;
  method: string;
  started_at?: number;
  finished_at?: number;
  baseline?: Record<string, any>;
  trials?: any[];
  /** P0-3 — Walk-Forward consistency (%) calculée par auto_optimizer._wf_consistent.
   * Stockée sur le job côté backend (auto_optimizer.py:577). */
  wf_consistency?: number;
  /** P0-2 — Deflated Sharpe Ratio (probabilité que le Sharpe soit réellement > 0,
   * corrigée du biais de sélection multiple). Calculé par opt_scoring.deflated_sharpe_ratio. */
  deflated_sharpe?: number;
  /** P0-2 — raison du refus apply (cf. beats_baseline). Présent si apply refusé. */
  apply_refused_reason?: string;
  result?: {
    best_params?: Record<string, any>;
    best_oos_score?: number;
    best_oos_pnl?: number;
    best_oos_trades?: number;
    best_oos_wr?: number;
    best_oos_sharpe?: number;
    /** O-01 : alias de best_oos_* — tranche de sélection, pas un holdout. */
    best_val_score?: number;
    best_val_pnl?: number;
    best_val_trades?: number;
    best_val_wr?: number;
    best_val_sharpe?: number;
    best_oos_dd?: number;
    best_oos_alpha?: number;
    overfit?: number;
    /** P0-2 — Deflated Sharpe dans le résultat (alias pour compat). */
    deflated_sharpe?: number;
    /** Alias backend : `top5` (cf. audit §3.3). */
    top5?: OptimizeTrial[];
    /** Frontend-friendly alias. */
    top_trials?: OptimizeTrial[];
    /** Bloc consolidé pour la before/after grid (OPT-001).
     *  Non renvoyé par le backend aujourd'hui — reconstruit côté frontend
     *  depuis `best_oos_*` si absent. */
    after?: {
      trades: number;
      pnl: number;
      sharpe: number;
      win_rate: number;
      max_drawdown: number;
      alpha: number;
    };
    /** Contexte facturé pendant toute l'optimisation (S11) — sans lui, deux
     *  `oos_score` ne sont pas comparables. */
    cost_model?: CostModel;
  };
  applied?: boolean;
  /** N-02 : 'holdout' si le gate a jugé le holdout, sinon 'selection'. */
  gate_source?: 'holdout' | 'selection' | string;
  error?: string;
  /** P2-8 : true si le TF utilisé n'est pas dans les TFs recommandés par la stratégie. */
  is_recommended?: boolean;
  /** P2-8 : TFs recommandés par la stratégie (stockés sur le job par auto_optimizer). */
  recommended_tfs?: string[];
}

/** OPT-002 — un trial dans le top-5. */
export interface OptimizeTrial {
  is_score: number;
  oos_score: number;
  final?: number;
  /** Alias backend : `final_score`. */
  final_score?: number;
  oos_pnl: number;
  oos_wr: number;
  oos_dd: number;
  overfit: number;
  params?: Record<string, any>;
}

export interface OptimizeSpaces {
  [strategy: string]: {
    params: Record<string, any>;
    /** Alias backend : `timeframes` = TFs recommandés par la stratégie. */
    timeframes: string[];
    recommended_tfs?: string[];
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
  /** P1-2 : job_id présent dans la liste /api/ml/jobs (pas dans /status single). */
  job_id?: string;
  kind: 'train' | 'sweep';
  status: 'running' | 'done' | 'error' | 'cancelled' | 'pending' | 'queued' | 'skipped';
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

// ── P1-4 : validation d'un paramétrage optimisé ─────────────────────────────
// `POST /api/optimize/validate` rejoue les `best_params` d'un job terminé.

/** Tenue de la STRATÉGIE sur les segments d'un régime donné. */
export interface RegimeStrategyPerf {
  n_trades: number;
  pnl: number;
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
