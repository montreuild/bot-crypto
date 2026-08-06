/**
 * BT-009 — export CSV des trades d'un backtest, 19 colonnes.
 *
 * Extrait du template Jinja2 `backtest.html:1048-1075` (`exportCSV`).
 * BOM UTF-8 en début de fichier pour Excel FR.
 */

import type { BacktestTrade } from '@/types';

const HEADERS = [
  'id',
  'side',
  'setup',
  'entry_time',
  'exit_time',
  'entry',
  'exit',
  'duration_bars',
  'exit_reason',
  'score',
  'pnl',
  'pnl_pct',
  'fees',
  'sl_atr_mult',
  'tp_atr_mult',
  'exit_after_bars',
  'size_factor',
  'regime_lbl',
  'bearish_excess',
] as const;

/** Type étendu pour les champs que le backend renvoie mais qui ne sont pas
 *  déclarés dans `BacktestTrade` (audit backend §1.3-1.6). */
type TradeRow = BacktestTrade & {
  id?: number | string;
  setup?: string;
  entry_time?: string;
  exit_time?: string;
  duration_bars?: number;
  exit_reason?: string;
  score?: number;
  sl_atr_mult?: number;
  tp_atr_mult?: number;
  exit_after_bars?: number;
  size_factor?: number;
  regime_lbl?: string;
  bearish_excess?: number;
  // Alias backend (cf. audit §1.6) : `reason` est le nom backend de `signal_reason`.
  signal_reason?: string;
};

function csvCell(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') {
    return `"${v.replace(/"/g, '""')}"`;
  }
  if (typeof v === 'number') {
    return Number.isFinite(v) ? String(v) : '';
  }
  // Objects/arrays → JSON
  return `"${JSON.stringify(v).replace(/"/g, '""')}"`;
}

export interface TradesCsvMeta {
  symbol: string;
  timeframe: string;
  strategy: string;
}

export function tradesToCsv(trades: TradeRow[], meta: TradesCsvMeta): string {
  const rows = trades.map((t) =>
    HEADERS.map((h) => {
      // `signal_reason` lit `t.signal_reason` puis fallback `t.reason` (backend).
      if (h === 'entry_time') return csvCell(t.entry_time ?? t.time);
      if (h === 'exit_reason') return csvCell(t.exit_reason ?? t.reason);
      // Pour les autres champs, on lit directement la clé.
      return csvCell((t as unknown as Record<string, unknown>)[h]);
    }).join(','),
  );
  // BOM UTF-8 + headers + rows
  return '\ufeff' + HEADERS.join(',') + '\n' + rows.join('\n');
}

export function downloadTradesCsv(trades: TradeRow[], meta: TradesCsvMeta): void {
  const csv = tradesToCsv(trades, meta);
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const ts = new Date().toISOString().replace(/[:.]/g, '').slice(0, 14);
  const filename = `trades_${meta.symbol}_${meta.timeframe}_${meta.strategy}_${ts}.csv`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
