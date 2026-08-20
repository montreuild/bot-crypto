/**
 * Contrats UI-only (FE-03) — pas de miroir Pydantic.
 * WebSocket live + primitives du chart SMC.
 */

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
  [key: string]: unknown;
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

export interface SmcOhlcv {
  time?: number[];
  open?: number[];
  high?: number[];
  low?: number[];
  close?: number[];
  volume?: number[];
}

export interface SmcTrendline {
  kind?: string;
  time1?: number;
  time2?: number;
  y1?: number;
  y2?: number;
}

export interface SmcChannel {
  time_start?: number;
  time_end?: number;
  half_width?: number;
  mid_start?: number;
  mid_end?: number;
}

export interface SmcStructurePoint {
  time?: number;
  price?: number;
}

export interface SmcCycle {
  from_time?: number;
  from_price?: number;
  target?: number;
  phase?: string;
  progress?: number;
  boundary?: string | number;
}

export interface SmcLiquidityPool {
  kind?: string;
  level?: number;
  status?: string;
}

export interface SmcVolumeProfile {
  poc?: number;
  hvns?: number[];
  lvns?: number[];
}

export interface SmcMarker {
  type?: string;
  time?: number;
  direction?: string;
  rejected?: boolean;
}

export interface SmcSwingLabel {
  time?: number;
  kind?: string;
  label?: string;
}

export interface SmcSignal {
  side?: string;
  score?: number;
  setup?: string;
  entry?: number;
  stop?: number;
  tp?: number;
  reason?: string;
}

export interface SmcBias {
  trend?: number;
  label?: string;
}

export interface SmcHtfBias {
  trend?: number;
  label?: string;
  n_htf?: number;
}

export interface SmcSession {
  name?: string;
  in_killzone?: boolean;
}

export interface SmcRealizedTrade {
  side?: string;
  entry?: number;
  stop?: number;
  setup?: string;
  signal_time?: number | null;
  [key: string]: unknown;
}

export interface SmcChartData {
  ohlcv?: SmcOhlcv;
  trendlines?: SmcTrendline[];
  channel?: SmcChannel;
  structure_line?: SmcStructurePoint[];
  cycle?: SmcCycle;
  liquidity_pools?: SmcLiquidityPool[];
  volume_profile?: SmcVolumeProfile;
  premium_discount?: unknown;
  markers?: SmcMarker[];
  swing_labels?: SmcSwingLabel[];
  trade_plans?: Array<Record<string, unknown>>;
  signal?: SmcSignal;
  htf_bias?: SmcHtfBias;
  session?: SmcSession;
  bias?: SmcBias;
  order_blocks?: Array<Record<string, unknown>>;
  fvgs?: Array<Record<string, unknown>>;
  liquidity_voids?: Array<Record<string, unknown>>;
  breakers?: Array<Record<string, unknown>>;
  rejection_blocks?: Array<Record<string, unknown>>;
  trades?: SmcRealizedTrade[];
  n_bars?: number;
  candles?: unknown[];
  voids?: unknown[];
  rejections?: unknown[];
  pools?: unknown[];
  structure?: Record<string, unknown>;
}

export interface BotThresholds {
  [key: string]: unknown;
}
