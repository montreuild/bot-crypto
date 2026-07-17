'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatPct } from '@/lib/utils';
import { toast } from 'sonner';
import { useRunReplay, useCancelReplay } from '@/hooks/use-api';
import {
  Play, Loader2, StopCircle, AlertCircle, BarChart3,
  TrendingUp, Clock,
} from 'lucide-react';
import {
  LineChart, Line, ResponsiveContainer, XAxis, YAxis,
  CartesianGrid, Tooltip,
} from 'recharts';
import type { ReplayResult } from '@/types';

// ── Helpers ─────────────────────────────────────────────────────────────────

function timeToLabel(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' });
}

function pnlColor(v: number): string {
  return v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-muted';
}

// ── OHLCV Chart per TF ──────────────────────────────────────────────────────

function OhlcvChart({
  data,
  color = '#22d3ee',
}: {
  data: { time: number[]; close: number[] };
  color?: string;
}) {
  const points = (data?.time || []).map((t, i) => ({
    time: timeToLabel(t),
    close: data.close?.[i] ?? null,
  }));
  if (points.length === 0) {
    return <div className="text-sm text-muted text-center py-6">Pas de données OHLCV</div>;
  }
  // Downsample if too many points for perf
  const step = points.length > 500 ? Math.ceil(points.length / 500) : 1;
  const sampled = points.filter((_, i) => i % step === 0);

  return (
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={sampled}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
          <XAxis dataKey="time" stroke="#6b7280" fontSize={10} minTickGap={40} />
          <YAxis
            stroke="#6b7280"
            fontSize={10}
            domain={['auto', 'auto']}
            tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
          />
          <Tooltip
            contentStyle={{ backgroundColor: '#141a23', border: '1px solid #1f2937', borderRadius: '8px' }}
            formatter={(v: any) => [`$${Number(v).toFixed(2)}`, 'Close']}
          />
          <Line
            type="monotone"
            dataKey="close"
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Strategy metrics table per TF ───────────────────────────────────────────

function StrategyMetricsTable({ byStrategy }: { byStrategy: Record<string, any> }) {
  const entries = Object.entries(byStrategy || {});
  if (entries.length === 0) {
    return <div className="text-xs text-muted text-center py-3">Aucune stratégie testée</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-dim border-b border-border">
            <th className="py-2 pr-2 font-medium">Stratégie</th>
            <th className="py-2 px-2 font-medium text-right">Trades</th>
            <th className="py-2 px-2 font-medium text-right">WR</th>
            <th className="py-2 px-2 font-medium text-right">PnL</th>
            <th className="py-2 px-2 font-medium text-right">Sharpe</th>
            <th className="py-2 px-2 font-medium text-right">MaxDD</th>
            <th className="py-2 pl-2 font-medium text-right">PF</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, s]) => (
            <tr key={name} className="border-b border-border/30 hover:bg-card-hover">
              <td className="py-2 pr-2 font-mono font-semibold">{name}</td>
              <td className="py-2 px-2 text-right font-mono text-muted">{s.trades ?? 0}</td>
              <td className={cn('py-2 px-2 text-right font-mono', (s.win_rate ?? 0) >= 50 ? 'text-emerald-400' : 'text-red-400')}>
                {(s.win_rate ?? 0).toFixed(1)}%
              </td>
              <td className={cn('py-2 px-2 text-right font-mono', pnlColor(s.pnl ?? 0))}>
                {(s.pnl ?? 0) >= 0 ? '+' : ''}{formatUSD(s.pnl ?? 0)}
              </td>
              <td className="py-2 px-2 text-right font-mono text-muted">{(s.sharpe ?? 0).toFixed(2)}</td>
              <td className="py-2 px-2 text-right font-mono text-red-400">{(s.max_drawdown ?? 0).toFixed(2)}%</td>
              <td className="py-2 pl-2 text-right font-mono text-muted">
                {s.profit_factor === 999 || s.profit_factor == null ? '∞' : s.profit_factor.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Cross-TF summary table ──────────────────────────────────────────────────

function CrossTfSummaryTable({ rows }: { rows: ReplayResult['cross_tf_summary'] }) {
  const sorted = [...(rows || [])].sort((a, b) => (b.pnl ?? 0) - (a.pnl ?? 0));
  if (sorted.length === 0) {
    return <div className="text-sm text-muted text-center py-4">Aucun résumé cross-TF</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-dim border-b border-border">
            <th className="p-3 font-medium">TF</th>
            <th className="p-3 font-medium">Stratégie</th>
            <th className="p-3 font-medium text-right">Bars</th>
            <th className="p-3 font-medium text-right">Days</th>
            <th className="p-3 font-medium text-right">Trades</th>
            <th className="p-3 font-medium text-right">WR</th>
            <th className="p-3 font-medium text-right">PnL</th>
            <th className="p-3 font-medium text-right">PnL %</th>
            <th className="p-3 font-medium text-right">Sharpe</th>
            <th className="p-3 font-medium text-right">MaxDD</th>
            <th className="p-3 font-medium text-right">PF</th>
            <th className="p-3 font-medium text-right">Equity</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr key={`${row.tf}-${row.strategy}-${i}`} className="border-b border-border/30 hover:bg-card-hover">
              <td className="p-3"><Badge variant="purple">{row.tf}</Badge></td>
              <td className="p-3 font-mono font-semibold">{row.strategy}</td>
              <td className="p-3 text-right font-mono text-muted">{row.n_bars ?? 0}</td>
              <td className="p-3 text-right font-mono text-muted">{row.days_covered ?? 0}</td>
              <td className="p-3 text-right font-mono text-muted">{row.trades ?? 0}</td>
              <td className={cn('p-3 text-right font-mono', (row.win_rate ?? 0) >= 50 ? 'text-emerald-400' : 'text-red-400')}>
                {(row.win_rate ?? 0).toFixed(1)}%
              </td>
              <td className={cn('p-3 text-right font-mono font-semibold', pnlColor(row.pnl ?? 0))}>
                {(row.pnl ?? 0) >= 0 ? '+' : ''}{formatUSD(row.pnl ?? 0)}
              </td>
              <td className={cn('p-3 text-right font-mono', pnlColor(row.pnl_pct ?? 0))}>
                {formatPct(row.pnl_pct ?? 0, 2, true)}
              </td>
              <td className="p-3 text-right font-mono text-muted">{(row.sharpe ?? 0).toFixed(2)}</td>
              <td className="p-3 text-right font-mono text-red-400">{(row.max_drawdown ?? 0).toFixed(2)}%</td>
              <td className="p-3 text-right font-mono text-muted">
                {row.profit_factor === 999 ? '∞' : (row.profit_factor ?? 0).toFixed(2)}
              </td>
              <td className="p-3 text-right font-mono">{formatUSD(row.final_equity ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function ReplayPage() {
  const runReplay = useRunReplay();
  const cancelReplay = useCancelReplay();
  const [result, setResult] = useState<ReplayResult | null>(null);

  const [symbol, setSymbol] = useState('BTC/USDC');
  const [months, setMonths] = useState(3);
  const [timeframes, setTimeframes] = useState('15m,1h,4h');
  const [strategies, setStrategies] = useState('');
  const [walkForward, setWalkForward] = useState(false);
  const [monteCarlo, setMonteCarlo] = useState(false);

  const handleRun = async () => {
    if (!symbol.trim()) {
      toast.error('Symbole requis');
      return;
    }
    if (months < 0.5 || months > 24) {
      toast.error('Months doit être entre 0.5 et 24');
      return;
    }
    try {
      toast.info('Replay en cours... (peut prendre 1-5 min)');
      const r = await runReplay.mutateAsync({
        symbol: symbol.trim(),
        months,
        timeframes: timeframes.trim() || undefined,
        strategies: strategies.trim() || undefined,
        walk_forward: walkForward,
        monte_carlo: monteCarlo,
      });
      setResult(r);
      toast.success(`Replay terminé · ${(r.timeframes_tested || []).length} TFs testés`);
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleCancel = async () => {
    try {
      await cancelReplay.mutateAsync();
      toast.success('Replay annulé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const byTfEntries = Object.entries(result?.by_timeframe || {});

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Replay Multi-Timeframe</h1>
          <p className="text-sm text-muted mt-1">
            Rejoue les stratégies sur plusieurs TFs simultanément · Walk-Forward + Monte-Carlo
          </p>
        </div>
        {result && (
          <Badge variant="info">
            <Clock className="w-3 h-3" />
            {result.months} mois · {result.symbol}
          </Badge>
        )}
      </div>

      {/* Config form */}
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
          <BarChart3 className="w-4 h-4 text-primary-400" />
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="text-xs text-dim block mb-1.5">Symbole</label>
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="BTC/USDC"
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Months (0.5-24)</label>
              <input
                type="number"
                step={0.5}
                min={0.5}
                max={24}
                value={months}
                onChange={(e) => setMonths(Math.min(24, Math.max(0.5, Number(e.target.value) || 0.5)))}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Timeframes (CSV)</label>
              <input
                value={timeframes}
                onChange={(e) => setTimeframes(e.target.value)}
                placeholder="15m,1h,4h"
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Strategies (CSV ou vide = toutes)</label>
              <input
                value={strategies}
                onChange={(e) => setStrategies(e.target.value)}
                placeholder="trend_rider,breakout"
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
          </div>

          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={walkForward}
                onChange={(e) => setWalkForward(e.target.checked)}
                className="rounded"
              />
              Walk-Forward
            </label>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={monteCarlo}
                onChange={(e) => setMonteCarlo(e.target.checked)}
                className="rounded"
              />
              Monte-Carlo
            </label>
          </div>

          <div className="flex gap-2">
            <Button
              onClick={handleRun}
              disabled={runReplay.isPending}
              variant="primary"
            >
              {runReplay.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" fill="currentColor" />
              )}
              Lancer le replay
            </Button>
            <Button
              onClick={handleCancel}
              disabled={cancelReplay.isPending}
              variant="danger"
            >
              <StopCircle className="w-4 h-4" />
              Annuler
            </Button>
          </div>

          {runReplay.isPending && (
            <div className="flex items-center gap-3 text-sm text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Exécution en cours — le replay multi-TF peut prendre 1 à 5 minutes selon le volume de données.</span>
            </div>
          )}

          {runReplay.isError && (
            <div className="flex items-center gap-3 text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
              <AlertCircle className="w-4 h-4" />
              <span>Erreur: {(runReplay.error as any)?.message || 'inconnue'}</span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Results */}
      {result && (
        <>
          {/* KPIs */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <KpiCard label="Symbol" value={result.symbol} />
            <KpiCard label="Months" value={`${result.months}`} />
            <KpiCard label="TFs testés" value={`${(result.timeframes_tested || []).length}`} />
            <KpiCard
              label="PnL total cross-TF"
              value={formatUSD((result.cross_tf_summary || []).reduce((s, r) => s + (r.pnl || 0), 0))}
              color={
                (result.cross_tf_summary || []).reduce((s, r) => s + (r.pnl || 0), 0) >= 0
                  ? 'text-emerald-400'
                  : 'text-red-400'
              }
            />
          </div>

          {/* Per-TF cards */}
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted mb-3">
              Résultats par Timeframe
            </h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {byTfEntries.map(([tf, info]) => (
                <Card key={tf}>
                  <CardHeader>
                    <CardTitle>{tf}</CardTitle>
                    <div className="flex items-center gap-2">
                      <Badge variant="purple">{info.n_bars ?? 0} bars</Badge>
                      {info.days_covered != null && (
                        <Badge variant="default">{info.days_covered} jours</Badge>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {info.gaps_warning && (
                      <div className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded p-2">
                        {info.gaps_warning}
                      </div>
                    )}
                    {info.ohlcv && info.ohlcv.close?.length > 0 && (
                      <OhlcvChart data={info.ohlcv} />
                    )}
                    <StrategyMetricsTable byStrategy={info.by_strategy} />
                  </CardContent>
                </Card>
              ))}
              {byTfEntries.length === 0 && (
                <Card className="lg:col-span-2">
                  <CardContent className="text-center text-muted py-8 text-sm">
                    Aucun résultat par TF
                  </CardContent>
                </Card>
              )}
            </div>
          </div>

          {/* Cross-TF summary */}
          <Card>
            <CardHeader>
              <CardTitle>Résumé cross-TF (trié par PnL)</CardTitle>
              <Badge variant="info">{(result.cross_tf_summary || []).length} lignes</Badge>
            </CardHeader>
            <CardContent className="p-0">
              <CrossTfSummaryTable rows={result.cross_tf_summary} />
            </CardContent>
          </Card>
        </>
      )}

      {!result && !runReplay.isPending && (
        <Card>
          <CardContent className="text-center py-16 text-muted text-sm">
            <TrendingUp className="w-10 h-10 mx-auto mb-3 text-dim" />
            Configurez les paramètres ci-dessus puis lancez un replay multi-timeframe.
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function KpiCard({
  label,
  value,
  color = 'text-foreground',
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <Card>
      <CardContent className="p-3">
        <div className="text-[10px] uppercase tracking-wider text-dim">{label}</div>
        <div className={cn('font-mono text-lg font-semibold mt-1', color)}>{value}</div>
      </CardContent>
    </Card>
  );
}
