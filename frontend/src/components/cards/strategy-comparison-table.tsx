/**
 * BT-007 — Tableau comparatif multi-stratégies (best value ✦).
 *
 * Extrait du template Jinja2 `backtest.html:757-768` (`buildCmp`).
 * 10 colonnes : Trades, Win Rate, PnL net, Max DD, Sharpe, Expectancy,
 * Profit Factor, Avg Win, Avg Loss, Alpha.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { BacktestResult } from '@/types';

interface Props {
  strategies: BacktestResult[];
}

function fmt(v: number | null | undefined, decimals = 2, prefix = '', suffix = ''): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${prefix}${v.toFixed(decimals)}${suffix}`;
}

function isBest(value: number | null | undefined, all: Array<number | null | undefined>, higherIsBetter = true): boolean {
  if (value == null) return false;
  const valid = all.filter((v) => v != null && Number.isFinite(v as number));
  if (valid.length === 0) return false;
  const target = higherIsBetter ? Math.max(...(valid as number[])) : Math.min(...(valid as number[]));
  return Math.abs((value as number) - target) < 1e-9;
}

export function StrategyComparisonTable({ strategies }: Props) {
  if (strategies.length < 2) return null;

  const allTrades = strategies.map((s) => s.total_trades);
  const allWr = strategies.map((s) => s.win_rate);
  const allPnl = strategies.map((s) => s.total_pnl);
  const allDd = strategies.map((s) => s.max_drawdown);
  const allSharpe = strategies.map((s) => s.sharpe);
  const allExp = strategies.map((s) => s.expectancy);
  const allPf = strategies.map((s) => s.profit_factor);
  const allAlpha = strategies.map((s) => s.alpha ?? 0);
  const allBestTrade = strategies.map((s) => s.best_trade);
  const allWorstTrade = strategies.map((s) => s.worst_trade);

  const row = (label: string, values: Array<number | null | undefined>, format: (v: number) => string, higherIsBetter = true) => (
    <tr className="border-b border-border/50">
      <td className="py-1 px-2 font-medium">{label}</td>
      {strategies.map((s, i) => {
        const v = values[i];
        const best = isBest(v, values, higherIsBetter);
        return (
          <td
            key={s.strategy}
            className={`text-right py-1 px-2 font-mono ${best ? 'bg-emerald-500/10 text-emerald-400 font-bold' : ''}`}
          >
            {v == null ? '—' : format(v as number)}
            {best && <span className="ml-1">✦</span>}
          </td>
        );
      })}
    </tr>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">📊 Comparatif — {strategies.length} stratégies</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left py-1 px-2">Métrique</th>
                {strategies.map((s) => (
                  <th key={s.strategy} className="text-right py-1 px-2 font-mono">
                    {s.strategy}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {row('Trades', allTrades, (v) => String(v))}
              {row('Win Rate', allWr, (v) => `${(v * 100).toFixed(1)}%`)}
              {row('PnL net', allPnl, (v) => fmt(v, 2, v >= 0 ? '+' : '', '$'))}
              {row('Max DD', allDd, (v) => `-${(v * 100).toFixed(1)}%`, false)}
              {row('Sharpe', allSharpe, (v) => v.toFixed(2))}
              {row('Expectancy', allExp, (v) => fmt(v, 2, v >= 0 ? '+' : '', '$'))}
              {row('Profit Factor', allPf, (v) => v.toFixed(2))}
              {row('Best Trade', allBestTrade, (v) => fmt(v, 2, '+', '$'))}
              {row('Worst Trade', allWorstTrade, (v) => fmt(v, 2, '', '$'), false)}
              {row('Alpha', allAlpha, (v) => fmt(v, 1, v >= 0 ? '+' : '', '%'))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
