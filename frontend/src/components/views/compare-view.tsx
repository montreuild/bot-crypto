'use client';

/**
 * Onglet Compare de `/lab` — comparaison côte à côte de plusieurs stratégies.
 *
 * Lot Laboratoire : cette vue était la page `/compare`, vers laquelle l'onglet
 * se contentait de renvoyer. `/compare` est désormais en 308 vers
 * `/lab?tab=compare`.
 */

import { useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatPct } from '@/lib/utils';
import { useBacktestSettings } from '@/hooks/use-api';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import {
  Loader2, Play, Download, BarChart3, XCircle, ArrowUpDown,
} from 'lucide-react';
import {
  LineChart, Line, ResponsiveContainer, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from 'recharts';
import type { BacktestResult } from '@/types';
import { TimeframeButtons } from '@/components/ui/timeframe-select';

const LIMITS = [100, 500, 1000, 2000];
const STRATEGY_COLORS = [
  '#22d3ee', '#10b981', '#f59e0b', '#8b5cf6',
  '#ef4444', '#3b82f6', '#ec4899', '#84cc16',
  '#f97316', '#06b6d4', '#a855f7', '#14b8a6',
];

const DEFAULT_STRATEGIES = [
  'pullback_trend', 'trend_rider', 'breakout', 'smart_money',
  'mean_revert', 'momentum',
];

type SortKey =
  | 'strategy' | 'total_trades' | 'win_rate' | 'total_pnl'
  | 'sharpe' | 'max_drawdown' | 'profit_factor'
  | 'expectancy' | 'best_trade' | 'worst_trade';

interface ResultRow {
  strategy: string;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  sharpe: number;
  max_drawdown: number;
  profit_factor: number;
  expectancy: number;
  best_trade: number;
  worst_trade: number;
  equity_curve: { time: string; equity: number }[];
  error?: string;
}

type SortDir = 'asc' | 'desc';

const COLUMNS: Array<{ key: SortKey; label: string; numeric: boolean; higherBetter: boolean }> = [
  { key: 'strategy',       label: 'Stratégie',     numeric: false, higherBetter: true },
  { key: 'total_trades',   label: 'Trades',        numeric: true,  higherBetter: true },
  { key: 'win_rate',       label: 'Win Rate',      numeric: true,  higherBetter: true },
  { key: 'total_pnl',      label: 'PnL Net',       numeric: true,  higherBetter: true },
  { key: 'sharpe',         label: 'Sharpe',        numeric: true,  higherBetter: true },
  { key: 'max_drawdown',   label: 'Max DD',        numeric: true,  higherBetter: false },
  { key: 'profit_factor',  label: 'Profit Factor', numeric: true,  higherBetter: true },
  { key: 'expectancy',     label: 'Expectancy',    numeric: true,  higherBetter: true },
  { key: 'best_trade',     label: 'Best Trade',    numeric: true,  higherBetter: true },
  { key: 'worst_trade',    label: 'Worst Trade',   numeric: true,  higherBetter: true },
];

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtCell(key: SortKey, value: any): string {
  if (value == null) return '—';
  switch (key) {
    case 'strategy': return String(value);
    case 'total_trades': return String(value);
    case 'win_rate': return `${Number(value).toFixed(1)}%`;
    case 'total_pnl':
    case 'expectancy':
    case 'best_trade':
    case 'worst_trade':
      return (key !== 'total_pnl' && Number(value) >= 0 ? '+' : '') + formatUSD(Number(value));
    case 'sharpe': return Number(value).toFixed(2);
    case 'max_drawdown': return `${Number(value).toFixed(2)}%`;
    case 'profit_factor': return Number(value) === 999 ? '∞' : Number(value).toFixed(2);
    default: return String(value);
  }
}

function cellColor(key: SortKey, value: any): string {
  if (value == null) return 'text-muted';
  switch (key) {
    case 'win_rate': return Number(value) >= 50 ? 'text-emerald-400' : 'text-red-400';
    case 'total_pnl':
    case 'expectancy':
    case 'best_trade':
      return Number(value) >= 0 ? 'text-emerald-400' : 'text-red-400';
    case 'worst_trade': return Number(value) >= 0 ? 'text-emerald-400' : 'text-red-400';
    case 'max_drawdown': return 'text-red-400';
    case 'sharpe': return Number(value) >= 1 ? 'text-emerald-400' : 'text-amber-400';
    case 'profit_factor':
      return Number(value) === 999 || Number(value) >= 1.5 ? 'text-emerald-400' : 'text-red-400';
    default: return 'text-foreground';
  }
}

// ── Vue ─────────────────────────────────────────────────────────────────────

export function CompareView() {
  const { data: settings } = useBacktestSettings();
  const availableStrategies = settings?.strategies || DEFAULT_STRATEGIES;

  const [symbol, setSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState('1h');
  const [limit, setLimit] = useState(500);
  const [selected, setSelected] = useState<string[]>(['pullback_trend', 'trend_rider', 'breakout']);

  const [running, setRunning] = useState(false);
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [sortKey, setSortKey] = useState<SortKey>('total_pnl');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const toggleStrategy = (s: string) => {
    setSelected((prev) => prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]);
  };

  const handleCompare = async () => {
    if (selected.length === 0) {
      toast.error('Sélectionnez au moins une stratégie');
      return;
    }
    setRunning(true);
    setRows([]);
    try {
      const settled = await Promise.allSettled(
        selected.map((strat) =>
          api.runBacktest({ strategy: strat, symbol, timeframe, limit }),
        ),
      );
      const out: ResultRow[] = settled.map((res, i) => {
        const strategy = selected[i];
        if (res.status === 'fulfilled') {
          const r: BacktestResult | BacktestResult[] = res.value;
          const bt: BacktestResult = Array.isArray(r) ? r[0] : r;
          return {
            strategy,
            total_trades: bt.total_trades ?? 0,
            win_rate: bt.win_rate ?? 0,
            total_pnl: bt.total_pnl ?? 0,
            sharpe: bt.sharpe ?? 0,
            max_drawdown: bt.max_drawdown ?? 0,
            profit_factor: bt.profit_factor ?? 0,
            expectancy: bt.expectancy ?? 0,
            best_trade: bt.best_trade ?? 0,
            worst_trade: bt.worst_trade ?? 0,
            equity_curve: bt.equity_curve || [],
          };
        }
        const reason = (res.reason as any)?.message || 'échec';
        return {
          strategy,
          total_trades: 0, win_rate: 0, total_pnl: 0, sharpe: 0,
          max_drawdown: 0, profit_factor: 0, expectancy: 0,
          best_trade: 0, worst_trade: 0,
          equity_curve: [],
          error: reason,
        };
      });
      setRows(out);
      const ok = out.filter((r) => !r.error).length;
      const ko = out.length - ok;
      if (ok > 0) {
        toast.success(`${ok} backtest(s) réussis${ko > 0 ? `, ${ko} échec(s)` : ''}`);
      } else {
        toast.error('Tous les backtests ont échoué');
      }
    } catch (e: any) {
      toast.error(`Erreur: ${e?.message || 'inconnue'}`);
    } finally {
      setRunning(false);
    }
  };

  const handleExportCsv = () => {
    if (rows.length === 0) {
      toast.error('Rien à exporter');
      return;
    }
    const headers = COLUMNS.map((c) => c.label);
    const lines = [headers.join(',')];
    for (const r of sortedRows) {
      const cells = COLUMNS.map((c) => {
        const v = (r as any)[c.key];
        if (v == null) return '';
        if (c.key === 'strategy') return `"${String(v).replace(/"/g, '""')}"`;
        return String(v);
      });
      lines.push(cells.join(','));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `compare_${symbol.replace('/', '-')}_${timeframe}_${limit}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success('CSV exporté');
  };

  // Sorting
  const sortedRows = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = (a as any)[sortKey];
      const bv = (b as any)[sortKey];
      let cmp: number;
      if (typeof av === 'string' || typeof bv === 'string') {
        cmp = String(av).localeCompare(String(bv));
      } else {
        cmp = Number(av ?? 0) - Number(bv ?? 0);
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return copy;
  }, [rows, sortKey, sortDir]);

  // Best value per numeric column
  const bestValues = useMemo(() => {
    const map: Record<string, number> = {};
    for (const col of COLUMNS) {
      if (!col.numeric) continue;
      const vals = rows.map((r) => (r as any)[col.key]).filter((v) => typeof v === 'number' && Number.isFinite(v));
      if (vals.length === 0) continue;
      map[col.key] = col.higherBetter ? Math.max(...vals) : Math.min(...vals);
    }
    return map;
  }, [rows]);

  // Equity curve merged data for Recharts
  const mergedEquity = useMemo(() => {
    if (rows.length === 0) return [];
    const maxLen = Math.max(...rows.map((r) => r.equity_curve?.length || 0));
    const out: Array<Record<string, number | string>> = [];
    for (let i = 0; i < maxLen; i++) {
      const row: Record<string, number | string> = { idx: i };
      for (const r of rows) {
        const eq = r.equity_curve?.[i]?.equity;
        if (typeof eq === 'number') row[r.strategy] = eq;
      }
      out.push(row);
    }
    return out;
  }, [rows]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'strategy' ? 'asc' : 'desc');
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Comparatif Stratégies</h2>
        <p className="text-sm text-muted mt-1">
          Comparez côte à côte plusieurs stratégies sur la même fenêtre de données
        </p>
      </div>

      {/* Config form */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="w-3.5 h-3.5 text-primary-400" />
            Configuration
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="text-xs text-dim block mb-1.5">Symbole</label>
              <input
                aria-label="Symbole"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
                placeholder="BTC/USDC"
              />
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Timeframe</label>
              <TimeframeButtons value={timeframe} onChange={setTimeframe} size="sm" />
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Bougies</label>
              <select
                aria-label="Bougies"
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
              >
                {LIMITS.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <div className="flex items-end">
              <Button
                onClick={handleCompare}
                disabled={running || selected.length === 0}
                variant="primary"
                className="w-full"
              >
                {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" fill="currentColor" />}
                Comparer
              </Button>
            </div>
          </div>

          {/* Strategy chips */}
          <div>
            <div className="text-xs text-dim mb-2">
              Stratégies ({selected.length} sélectionnée{selected.length > 1 ? 's' : ''})
            </div>
            <div className="flex flex-wrap gap-2">
              {availableStrategies.map((s: string, i: number) => {
                const isSel = selected.includes(s);
                const color = STRATEGY_COLORS[i % STRATEGY_COLORS.length];
                return (
                  <button
                    key={s}
                    onClick={() => toggleStrategy(s)}
                    className={cn(
                      'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-all',
                      isSel
                        ? 'bg-card-hover border-border-hi text-foreground'
                        : 'bg-transparent border-border text-muted hover:text-foreground hover:border-border-hi',
                    )}
                  >
                    {isSel ? (
                      <span
                        className="w-2 h-2 rounded-full"
                        style={{ backgroundColor: color }}
                      />
                    ) : (
                      <span className="w-2 h-2 rounded-full border border-border-hi" />
                    )}
                    {s}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Loading indicator */}
          {running && (
            <div className="flex items-center gap-3 text-sm text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>
                Exécution de {selected.length} backtest(s) en parallèle...
              </span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Results table */}
      {rows.length > 0 && !running && (
        <Card>
          <CardHeader>
            <CardTitle>Résultats comparatifs</CardTitle>
            <Button size="sm" variant="outline" onClick={handleExportCsv}>
              <Download className="w-3.5 h-3.5" />
              Export CSV
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-dim border-b border-border">
                    {COLUMNS.map((col) => {
                      const isSorted = sortKey === col.key;
                      return (
                        <th
                          key={col.key}
                          onClick={() => toggleSort(col.key)}
                          className={cn(
                            'p-3 font-medium cursor-pointer select-none hover:text-foreground whitespace-nowrap',
                            col.numeric ? 'text-right' : 'text-left',
                          )}
                        >
                          <span className="inline-flex items-center gap-1">
                            {col.label}
                            <ArrowUpDown className={cn('w-3 h-3', isSorted ? 'text-primary-400' : 'text-dim/50')} />
                          </span>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {sortedRows.map((r, idx) => {
                    const color = STRATEGY_COLORS[selected.indexOf(r.strategy) % STRATEGY_COLORS.length];
                    return (
                      <tr key={`${r.strategy}-${idx}`} className="border-b border-border/30 hover:bg-card-hover">
                        {COLUMNS.map((col) => {
                          const v = (r as any)[col.key];
                          const isBest = col.numeric
                            && bestValues[col.key] != null
                            && Number(v) === bestValues[col.key]
                            && !r.error;
                          return (
                            <td
                              key={col.key}
                              className={cn(
                                'p-3 font-mono whitespace-nowrap',
                                col.numeric ? 'text-right' : 'text-left',
                                isBest && 'bg-emerald-500/10',
                                cellColor(col.key, v),
                              )}
                            >
                              {col.key === 'strategy' ? (
                                <span className="inline-flex items-center gap-2 font-sans font-semibold">
                                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                                  {r.error ? <span className="text-muted line-through">{v}</span> : v}
                                  {r.error && (
                                    <span title={r.error}>
                                      <XCircle className="w-3.5 h-3.5 text-red-400" />
                                    </span>
                                  )}
                                </span>
                              ) : r.error ? (
                                <span className="text-dim">—</span>
                              ) : (
                                fmtCell(col.key, v)
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Equity curves overlaid */}
      {rows.length > 0 && !running && mergedEquity.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Equity curves superposées</CardTitle>
            <Badge variant="info">{rows.filter((r) => !r.error).length} stratégies</Badge>
          </CardHeader>
          <CardContent>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={mergedEquity}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                  <XAxis dataKey="idx" stroke="#6b7280" fontSize={10} />
                  <YAxis
                    stroke="#6b7280"
                    fontSize={10}
                    tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
                  />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#141a23', border: '1px solid #1f2937', borderRadius: '8px' }}
                    formatter={(v: any, name: string) => [`$${Number(v).toFixed(2)}`, name]}
                    labelFormatter={(l) => `Trade ${l}`}
                  />
                  <Legend
                    wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
                    iconType="line"
                  />
                  {rows.filter((r) => !r.error).map((r) => {
                    const color = STRATEGY_COLORS[selected.indexOf(r.strategy) % STRATEGY_COLORS.length];
                    return (
                      <Line
                        key={r.strategy}
                        type="monotone"
                        dataKey={r.strategy}
                        stroke={color}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                        connectNulls
                      />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {rows.length === 0 && !running && (
        <Card>
          <CardContent className="text-center py-16 text-muted text-sm">
            <BarChart3 className="w-10 h-10 mx-auto mb-3 text-dim" />
            Configurez les paramètres et sélectionnez des stratégies pour lancer la comparaison.
          </CardContent>
        </Card>
      )}
    </div>
  );
}
