/**
 * Contrats dérivés des `response_model` serveur (API-01 / FE-03).
 * Source : `app/api/schemas.py`. Régénérer après un changement de schéma :
 *   python scripts/gen_frontend_types.py
 * Ne pas recopier à la main dans index.ts — importer d'ici.
 */

export interface TimeframeQuery {
  timeframe: string;
}

export interface SymbolQuery {
  symbol: string;
}

export interface StrategyParamsBody {
  strategy: string;
  params?: Record<string, unknown>;
  timeframe?: string | null;
  symbol?: string | null;
}

export interface StrategyTimeframeBody {
  strategy: string;
  timeframe: string;
  enabled?: boolean;
  symbol?: string | null;
}

export interface TradingParamsBody {
  score_threshold?: number | null;
  paper_mode?: boolean | null;
  paper_slippage?: number | null;
  daily_drawdown_limit?: number | null;
  max_drawdown_global?: number | null;
}

export interface MarginConfigBody {
  margin?: boolean | null;
  margin_mode?: string | null;
  max_leverage?: number | null;
}

export interface RiskConfigBody {
  consecutive_loss_limit?: number | null;
  slot_daily_dd_limit?: number | null;
  win_rate_floor?: number | null;
  volatility_threshold?: number | null;
  consecutive_pause_secs?: number | null;
  min_slot_weight?: number | null;
}

export interface VenueEnvelopeBody {
  capital?: number | null;
  max_symbol_exposure_pct?: number | null;
  symbol_risk_pct?: number | null;
  venue_risk_pct?: number | null;
}

export interface RiskEnvelopesBody {
}

export interface StrategiesEnabledBody {
  enabled?: Array<string>;
}

export interface TimeframesBody {
  timeframes?: Array<string>;
}

export interface AutoOptimizerBody {
  enabled?: boolean;
  interval_h?: number;
}

export interface NotificationsConfigBody {
  telegram_enabled?: boolean | null;
  telegram_bot_token?: string | null;
  telegram_chat_id?: string | null;
  whatsapp_enabled?: boolean | null;
  whatsapp_number?: string | null;
  whatsapp_token?: string | null;
  email_enabled?: boolean | null;
  email_smtp?: string | null;
  email_port?: number | null;
  email_user?: string | null;
  email_password?: string | null;
  email_to?: string | null;
  min_pnl_to_notify?: number | null;
  position_loss_warn_pct?: number | null;
}

export interface BacktestResultModel {
  initial_capital: number;
  final_equity: number;
  total_pnl: number;
  net_profit: number;
  total_trades: number;
  win_rate: number;
  max_drawdown: number;
  sharpe?: number | null;
  profit_factor?: number | null;
  realistic_risk?: boolean;
  fallback_to_inline?: boolean;
  trades?: Array<unknown>;
  cost_model?: Record<string, unknown> | null;
}

export interface BacktestRunResponse {
  symbol: string;
  timeframe: string;
  n_bars?: number;
  realistic_risk?: boolean;
  cost_model?: Record<string, unknown> | null;
  by_strategy?: Record<string, unknown>;
}

export interface PortfolioResponse {
  running: boolean;
  capital?: number | null;
  paper_mode?: boolean | null;
  allocation?: Array<unknown>;
  risk?: Record<string, unknown>;
  activity?: Array<unknown>;
}

export interface TradeRow {
  id?: unknown;
  time?: string | null;
  symbol?: string | null;
  side?: string | null;
  strategy?: string | null;
  pnl?: number | null;
  quote_currency?: string | null;
}

export interface TradesListResponse {
  total: number;
  offset: number;
  limit: number;
  trades?: Array<TradeRow>;
}

export interface RiskOverviewResponse {
  venues?: Array<Record<string, unknown>>;
  symbols?: Array<Record<string, unknown>>;
  slots?: Array<Record<string, unknown>>;
  total_risk_engaged?: number;
  rejections?: Record<string, unknown>;
  envelopes_config?: Record<string, unknown>;
}

export interface OptimizeResultsResponse {
  by_strategy_tf?: Record<string, unknown>;
  active_per_tf?: Record<string, unknown>;
}

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

export interface Position {
  id: string;
  symbol: string;
  side: string;
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
  quote_currency?: string;
}

export interface EquityPoint {
  time: string;
  equity: number;
}

export interface BacktestRecommendation {
  severity: string;
  code: string;
  title: string;
  message: string;
  action: string;
  action_link?: string;
  metrics?: Record<string, unknown>;
}

export interface BacktestRecoCounts {
  critical: number;
  warning: number;
  info: number;
  positive: number;
}

export interface BacktestRecommendationsSummary {
  counts: BacktestRecoCounts;
  verdict: string;
  verdict_label: string;
  total: number;
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
  sharpe: number | null;
  max_drawdown: number;
  expectancy?: number;
  total_borrow_cost?: number;
  best_trade?: number;
  worst_trade?: number;
  initial_capital?: number;
  buy_and_hold_pnl?: number;
  alpha?: number;
  alpha_vs_bh?: number | null;
  data_completeness?: number | null;
  data_warning?: string | null;
  equity_curve?: Array<EquityPoint>;
  recommendations?: Array<BacktestRecommendation>;
  recommendations_summary?: BacktestRecommendationsSummary | null;
  monte_carlo?: Record<string, unknown>;
}

export interface SignalLogEntry {
  time: string;
  symbol: string;
  strategy: string;
  side: string;
  score: number;
  threshold: number;
  timeframe: string;
  status: string;
  reason: string;
  entry?: number | null;
  exit?: number | null;
  pnl?: number | null;
  pnl_pct?: number | null;
}

export interface SlotBudget {
  slot_key: string;
  strategy: string;
  tf: string;
  symbol?: string | null;
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
  weight: number;
  envelope: number;
  max_notional: number;
  risk_amount: number;
  risk_engaged: number;
  notional_engaged: number;
  edge_ci_low?: number | null;
}

export interface RiskRejections {
  total?: number | null;
  par_motif?: Record<string, number>;
  par_slot?: Record<string, number>;
  par_symbole?: Record<string, number>;
}

export interface VenueEnvelopeConfig {
  capital?: number | null;
  max_symbol_exposure_pct?: number | null;
  symbol_risk_pct?: number | null;
  venue_risk_pct?: number | null;
}

export interface RiskOverview {
  venues?: Array<RiskVenue>;
  symbols?: Array<RiskSymbol>;
  slots?: Array<RiskSlot>;
  total_risk_engaged?: number;
  rejections?: RiskRejections;
  envelopes_config?: Record<string, VenueEnvelopeConfig>;
}

export interface RiskDiagnostic {
  severity: string;
  code: string;
  scope: string;
  message: string;
  values: Record<string, unknown>;
}

export interface RiskDiagnostics {
  diagnostics: Array<RiskDiagnostic>;
  errors: number;
  warnings: number;
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
  last_trades: Array<boolean>;
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
  parent_slot?: string | null;
  lineage: Array<string>;
}

export interface LifecycleSnapshot {
  states: Record<string, string>;
  counts: Record<string, number>;
  reopt_queue: Array<string>;
}

export interface BotStatus {
  status: string;
  paper_mode: boolean;
  timeframe: string;
  timeframes: Array<string>;
  strategies: Array<string>;
  capital?: number;
  cycle?: number;
  total_pnl?: number;
  total_pnl_pct?: number;
  total_trades?: number;
  win_rate?: number;
  profit_factor?: number;
  total_fees?: number;
  best_trade?: number;
  positions?: Array<Position>;
  by_strategy?: Record<string, StrategyStats>;
  signal_log?: Array<SignalLogEntry>;
  active_per_tf?: Record<string, Array<string>>;
  circuit_breaker_active?: boolean;
  circuit_breaker_reason?: string;
  daily_pnl_pct?: number;
  global_dd_pct?: number;
  current_risk?: number;
  daily_dd_limit?: number;
  global_dd_limit?: number;
  capital_allocation?: Array<SlotBudget>;
  circuit_breakers?: Array<CircuitBreakerStatus>;
  slot_states?: Array<SlotState>;
  volatility_brake?: boolean;
  margin_enabled?: boolean;
  margin_level?: number | null;
  margin_interest?: number;
  margin_mode?: string | null;
  balance_detail?: BalanceDetail | null;
  bots?: Array<BotIdentity>;
  lifecycle?: LifecycleSnapshot | null;
  shadow_allocation?: Record<string, unknown>;
  last_scan_time?: string;
  last_symbols_scanned?: Array<string>;
  git_commit?: string;
}

export interface Trade {
  id: unknown;
  time: string;
  symbol: string;
  side: string;
  strategy: string;
  timeframe?: string | null;
  entry: number;
  exit: number;
  pnl: number;
  pnl_pct: number;
  fees: number;
  status: string;
  score: number;
  reason: string;
  quote_currency?: string;
}

export interface CandleDatasetStats {
  symbol: string;
  tf: string;
  bars: number;
  size_kb?: number;
  gaps?: number;
  completeness?: number | null;
  from?: string | null;
  to?: string | null;
}

export interface CandlesStatsResponse {
  store: Array<CandleDatasetStats>;
}

export interface MLTrainRequest {
  recipe?: string | null;
  strategy?: string | null;
  symbol?: string;
  tf: string;
  symbols?: Array<string> | null;
  universe?: string | null;
  max_symbols?: number;
  compare_solo?: number;
  window_bars?: number | null;
  as_of?: string | null;
  params?: Record<string, unknown>;
  publish?: boolean;
}

export interface MLTrainStarted {
  job_id: string;
}
