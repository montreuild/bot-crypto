'use client';

import { useBacktestSettings, useRunBacktest } from '@/hooks/use-api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatPct } from '@/lib/utils';
import { toast } from 'sonner';
import { Play, Loader2, BarChart3, TrendingUp, TrendingDown } from 'lucide-react';
import { useState } from 'react';
import {
  AreaChart, Area, BarChart, Bar, ResponsiveContainer, XAxis, YAxis,
  CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts';
import type { BacktestResult } from '@/types';

export default function BacktestPage() {
  const { data: settings } = useBacktestSettings();
  const runBacktest = useRunBacktest();

  const [strategy, setStrategy] = useState('pullback_trend');
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState('1h');
  const [limit, setLimit] = useState(500);
  const [walkForward, setWalkForward] = useState(false);
  const [monteCarlo, setMonteCarlo] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);

  const handleRun = async () => {
    try {
      toast.info('Backtest en cours...');
      const res = await runBacktest.mutateAsync({
        strategy, symbol, timeframe, limit,
        walk_forward: walkForward,
        monte_carlo: monteCarlo,
      });
      const arr = Array.isArray(res) ? res[0] : res;
      setResult(arr);
      toast.success(`Backtest terminé: ${arr.total_trades} trades, ${arr.win_rate.toFixed(1)}% WR`);
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const strategies = settings?.strategies || ['pullback_trend', 'trend_rider', 'breakout', 'smart_money'];
  const symbols = settings?.symbols || ['BTC/USDC', 'ETH/USDC'];
  const timeframes = settings?.timeframes || ['15m', '1h', '4h', '1d'];

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Backtest</h1>
        <p className="text-sm text-muted mt-1">
          Testez vos stratégies sur données historiques avec Walk-Forward et Monte-Carlo
        </p>
      </div>

      {/* Config */}
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="text-xs text-dim block mb-1.5">Stratégie</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
              >
                {strategies.map((s: string) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Symbole</label>
              <select
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
              >
                {symbols.map((s: string) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Timeframe</label>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
              >
                {timeframes.map((tf: string) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Bougies</label>
              <select
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
              >
                <option value={100}>100</option>
                <option value={500}>500</option>
                <option value={1000}>1000</option>
                <option value={2000}>2000</option>
                <option value={5000}>5000</option>
              </select>
            </div>
          </div>

          <div className="flex items-center gap-6 mt-4">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={walkForward}
                onChange={(e) => setWalkForward(e.target.checked)}
                className="rounded"
              />
              Walk-Forward (5 folds)
            </label>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={monteCarlo}
                onChange={(e) => setMonteCarlo(e.target.checked)}
                className="rounded"
              />
              Monte-Carlo (200 runs)
            </label>
          </div>

          <Button
            onClick={handleRun}
            disabled={runBacktest.isPending}
            className="mt-4"
            variant="primary"
          >
            {runBacktest.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4" fill="currentColor" />
            )}
            Lancer le backtest
          </Button>
        </CardContent>
      </Card>

      {/* Results */}
      {result && (
        <>
          {/* KPIs */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            <KPI label="Trades" value={result.total_trades.toString()} />
            <KPI label="Win Rate" value={`${result.win_rate.toFixed(1)}%`} color={result.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'} />
            <KPI label="PnL Net" value={formatUSD(result.total_pnl)} color={result.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'} />
            <KPI label="Sharpe" value={result.sharpe.toFixed(2)} color={result.sharpe >= 1 ? 'text-emerald-400' : 'text-amber-400'} />
            <KPI label="Profit Factor" value={result.profit_factor === 999 ? '∞' : result.profit_factor.toFixed(2)} color={result.profit_factor >= 1.5 ? 'text-emerald-400' : 'text-red-400'} />
            <KPI label="Max DD" value={`${result.max_drawdown.toFixed(2)}%`} color="text-red-400" />
            <KPI label="Expectancy" value={formatUSD(result.expectancy)} color={result.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'} />
          </div>

          {/* Equity curve */}
          {result.equity_curve && result.equity_curve.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Equity Curve</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={result.equity_curve}>
                      <defs>
                        <linearGradient id="btEquity" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.4} />
                          <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                      <XAxis dataKey="time" stroke="#6b7280" fontSize={10} />
                      <YAxis stroke="#6b7280" fontSize={10} tickFormatter={(v) => `$${v.toFixed(0)}`} />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#141a23', border: '1px solid #1f2937', borderRadius: '8px' }}
                        formatter={(v: any) => [`$${Number(v).toFixed(2)}`, 'Equity']}
                      />
                      <Area type="monotone" dataKey="equity" stroke="#22d3ee" strokeWidth={2} fill="url(#btEquity)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Trades table */}
          {result.trades && result.trades.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Trades ({result.trades.length})</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto max-h-96">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-card">
                      <tr className="text-left text-xs text-dim border-b border-border">
                        <th className="p-3 font-medium">Time</th>
                        <th className="p-3 font-medium">Side</th>
                        <th className="p-3 font-medium text-right">Entry</th>
                        <th className="p-3 font-medium text-right">Exit</th>
                        <th className="p-3 font-medium text-right">PnL</th>
                        <th className="p-3 font-medium">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.trades.slice(0, 50).map((t, i) => (
                        <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                          <td className="p-3 text-xs text-muted font-mono">{t.time}</td>
                          <td className="p-3">
                            <span className={cn('text-xs font-semibold', t.side === 'long' ? 'text-emerald-400' : 'text-red-400')}>
                              {t.side.toUpperCase()}
                            </span>
                          </td>
                          <td className="p-3 text-right font-mono">${t.entry.toFixed(2)}</td>
                          <td className="p-3 text-right font-mono">${t.exit.toFixed(2)}</td>
                          <td className={cn('p-3 text-right font-mono', t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                            {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                          </td>
                          <td className="p-3 text-xs text-muted">{t.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function KPI({ label, value, color = 'text-foreground' }: { label: string; value: string; color?: string }) {
  return (
    <Card>
      <CardContent className="p-3">
        <div className="text-[10px] uppercase tracking-wider text-dim">{label}</div>
        <div className={cn('font-mono text-lg font-semibold mt-1', color)}>{value}</div>
      </CardContent>
    </Card>
  );
}
