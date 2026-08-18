/**
 * Contrats dérivés des `response_model` serveur (API-01 / FE-03).
 * Source : `app/api/schemas.py`. Régénérer après un changement de schéma :
 *   python scripts/export_openapi.py
 * Ne pas recopier à la main dans index.ts — importer d'ici.
 */

export interface BacktestRunResponse {
  symbol: string;
  timeframe: string;
  n_bars: number;
  realistic_risk: boolean;
  cost_model?: Record<string, unknown> | null;
  by_strategy: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PortfolioResponse {
  running: boolean;
  capital?: number | null;
  paper_mode?: boolean | null;
  allocation: unknown[];
  risk: Record<string, unknown>;
  activity: unknown[];
  [key: string]: unknown;
}

export interface TradesListResponse {
  total: number;
  offset: number;
  limit: number;
  trades: Array<{
    id?: unknown;
    time?: string | null;
    symbol?: string | null;
    side?: string | null;
    strategy?: string | null;
    pnl?: number | null;
    [key: string]: unknown;
  }>;
}

export interface RiskOverviewResponse {
  venues: Array<Record<string, unknown>>;
  symbols: Array<Record<string, unknown>>;
  slots: Array<Record<string, unknown>>;
  total_risk_engaged: number;
  rejections: Record<string, unknown>;
  envelopes_config: Record<string, unknown>;
  [key: string]: unknown;
}

export interface OptimizeResultsResponse {
  by_strategy_tf: Record<string, unknown>;
  active_per_tf: Record<string, unknown>;
  [key: string]: unknown;
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
  realistic_risk: boolean;
  fallback_to_inline: boolean;
  trades: unknown[];
  cost_model?: Record<string, unknown> | null;
  [key: string]: unknown;
}
