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
  manual_active: boolean;
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
