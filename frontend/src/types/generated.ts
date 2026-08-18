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
}

export interface RiskEnvelopesBody {
  [key: string]: unknown;
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
  [key: string]: unknown;
}

export interface BacktestRunResponse {
  symbol: string;
  timeframe: string;
  n_bars?: number;
  realistic_risk?: boolean;
  cost_model?: Record<string, unknown> | null;
  by_strategy?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PortfolioResponse {
  running: boolean;
  capital?: number | null;
  paper_mode?: boolean | null;
  allocation?: Array<unknown>;
  risk?: Record<string, unknown>;
  activity?: Array<unknown>;
  [key: string]: unknown;
}

export interface TradeRow {
  id?: unknown;
  time?: string | null;
  symbol?: string | null;
  side?: string | null;
  strategy?: string | null;
  pnl?: number | null;
  quote_currency?: string | null;
  [key: string]: unknown;
}

export interface TradesListResponse {
  total: number;
  offset: number;
  limit: number;
  trades?: Array<TradeRow>;
  [key: string]: unknown;
}

export interface RiskOverviewResponse {
  venues?: Array<Record<string, unknown>>;
  symbols?: Array<Record<string, unknown>>;
  slots?: Array<Record<string, unknown>>;
  total_risk_engaged?: number;
  rejections?: Record<string, unknown>;
  envelopes_config?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface OptimizeResultsResponse {
  by_strategy_tf?: Record<string, unknown>;
  active_per_tf?: Record<string, unknown>;
  [key: string]: unknown;
}
