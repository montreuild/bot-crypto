import { formatUSD } from '@/lib/utils';

export interface OverlayToggles {
  orderBlocks: boolean;
  liquidityPools: boolean;
  fvg: boolean;
  liquidityVoids: boolean;
  breakers: boolean;
  rejectionBlocks: boolean;
  trendlines: boolean;
  channel: boolean;
  structure: boolean;
  swingLabels: boolean;
  structureLine: boolean;
  premiumDiscount: boolean;
  volumeProfile: boolean;
  cycle: boolean;
}

export interface SeriesPoint {
  time: number;
  value: number;
}

export interface SmcZoneRow {
  kind?: string;
  top?: number;
  bottom?: number;
  status?: string;
  time_start?: string | number;
}

export interface ChartIndicators {
  ema20?: SeriesPoint[];
  ema50?: SeriesPoint[];
  ema200?: SeriesPoint[];
  bb_upper?: SeriesPoint[];
  bb_mid?: SeriesPoint[];
  bb_lower?: SeriesPoint[];
  rsi?: SeriesPoint[];
  macd?: SeriesPoint[];
  macd_signal?: SeriesPoint[];
  macd_hist?: SeriesPoint[];
}

export interface PremiumDiscount {
  high: number | null;
  low: number | null;
  equilibrium: number | null;
  zone: string;
  ote_low: number | null;
  ote_high: number | null;
}

export function formatPrice(v: unknown, decimals = 2): string {
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n) || n === 0) return '—';
  return formatUSD(n, { decimals });
}

/** Bougie d'ancrage d'un trade : exacte, sinon 1re ≥ signal, sinon dernière. */
export function resolveSignalTimestamp(
  times: number[],
  signalTime: number | null | undefined,
): number | null {
  if (!times.length) return null;
  const raw = Number(signalTime);
  if (Number.isFinite(raw) && raw > 0) {
    if (times.includes(raw)) return raw;
    const after = times.find((t) => t >= raw);
    return after ?? times[times.length - 1];
  }
  return times[times.length - 1];
}

export function normalizePd(raw: unknown): PremiumDiscount | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  const high = Number(o.premium_top ?? o.range_high ?? o.high);
  const low = Number(o.discount_bottom ?? o.range_low ?? o.low);
  const eq = Number(o.equilibrium);
  const zone = String(o.current_zone ?? o.zone ?? '').toLowerCase();
  return {
    high: Number.isFinite(high) && high !== 0 ? high : null,
    low: Number.isFinite(low) && low !== 0 ? low : null,
    equilibrium: Number.isFinite(eq) && eq !== 0 ? eq : null,
    zone,
    ote_low: Number.isFinite(Number(o.ote_low)) ? Number(o.ote_low) : null,
    ote_high: Number.isFinite(Number(o.ote_high)) ? Number(o.ote_high) : null,
  };
}
