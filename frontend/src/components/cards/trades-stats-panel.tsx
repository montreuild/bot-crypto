/**
 * BT-008 — Panneau de statistiques agrégées des trades.
 *
 * Extrait du template Jinja2 `backtest.html:861-926` (chips + tableaux
 * par setup et par raison de sortie).
 */

import { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { exitReasonBadge } from '@/lib/exit-reason-badges';
import type { BacktestTrade } from '@/types';

interface Props {
  trades: BacktestTrade[];
}

export function TradesStatsPanel({ trades }: Props) {
  const stats = useMemo(() => {
    const closed = trades.filter((t) => t.status !== 'open');
    const longs = closed.filter((t) => t.side === 'long');
    const shorts = closed.filter((t) => t.side === 'short');
    const wins = closed.filter((t) => (t.pnl ?? 0) > 0);
    const losses = closed.filter((t) => (t.pnl ?? 0) <= 0);
    const longWins = longs.filter((t) => (t.pnl ?? 0) > 0);
    const shortWins = shorts.filter((t) => (t.pnl ?? 0) > 0);
    const avgWin = wins.length ? wins.reduce((s, t) => s + (t.pnl ?? 0), 0) / wins.length : null;
    const avgLoss = losses.length ? losses.reduce((s, t) => s + (t.pnl ?? 0), 0) / losses.length : null;
    const wrLong = longs.length ? (longWins.length / longs.length) * 100 : null;
    const wrShort = shorts.length ? (shortWins.length / shorts.length) * 100 : null;

    const bySetup: Record<string, { n: number; wins: number; pnl: number }> = {};
    if (closed.some((t) => t.setup)) {
      closed.forEach((t) => {
        const k = t.setup || '—';
        if (!bySetup[k]) bySetup[k] = { n: 0, wins: 0, pnl: 0 };
        bySetup[k].n++;
        if ((t.pnl ?? 0) > 0) bySetup[k].wins++;
        bySetup[k].pnl += t.pnl ?? 0;
      });
    }

    const byExit: Record<string, { n: number; wins: number; pnl: number }> = {};
    closed.forEach((t) => {
      const k = t.exit_reason ?? t.reason ?? '—';
      if (!byExit[k]) byExit[k] = { n: 0, wins: 0, pnl: 0 };
      byExit[k].n++;
      if ((t.pnl ?? 0) > 0) byExit[k].wins++;
      byExit[k].pnl += t.pnl ?? 0;
    });

    return {
      longs: longs.length,
      shorts: shorts.length,
      wrLong,
      wrShort,
      avgWin,
      avgLoss,
      bySetup: Object.entries(bySetup).sort((a, b) => b[1].pnl - a[1].pnl),
      byExit: Object.entries(byExit).sort((a, b) => b[1].n - a[1].n),
      hasSetup: closed.some((t) => t.setup),
    };
  }, [trades]);

  const chip = (label: string, val: string | number, color = '') => (
    <div className="bg-surface border border-border rounded-md px-3 py-1.5 min-w-[90px]">
      <div className="text-[0.55rem] uppercase tracking-wide text-muted-foreground font-semibold mb-0.5">
        {label}
      </div>
      <div className={`font-mono text-sm font-bold ${color}`}>{val}</div>
    </div>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">📊 Statistiques des trades</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {chip('Long', stats.longs, 'text-cyan-400')}
          {chip('Short', stats.shorts, 'text-rose-400')}
          {chip('WR Long', stats.wrLong != null ? `${stats.wrLong.toFixed(1)}%` : '—')}
          {chip('WR Short', stats.wrShort != null ? `${stats.wrShort.toFixed(1)}%` : '—')}
          {chip('Avg Win', stats.avgWin != null ? `$${stats.avgWin.toFixed(2)}` : '—', 'text-emerald-400')}
          {chip('Avg Loss', stats.avgLoss != null ? `$${stats.avgLoss.toFixed(2)}` : '—', 'text-rose-400')}
        </div>

        {stats.hasSetup && stats.bySetup.length > 0 && (
          <div>
            <div className="text-xs text-muted-foreground mb-1">Par setup</div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-1">Setup</th>
                  <th className="text-right py-1 px-2">N</th>
                  <th className="text-right py-1 px-2">WR%</th>
                  <th className="text-right py-1 px-2">PnL total</th>
                </tr>
              </thead>
              <tbody>
                {stats.bySetup.map(([setup, s]) => (
                  <tr key={setup} className="border-b border-border/50">
                    <td className="py-1 font-mono text-purple-400">{setup}</td>
                    <td className="text-right py-1 px-2 font-mono">{s.n}</td>
                    <td className="text-right py-1 px-2 font-mono">
                      {((s.wins / s.n) * 100).toFixed(1)}%
                    </td>
                    <td className={`text-right py-1 px-2 font-mono ${s.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {stats.byExit.length > 0 && (
          <div>
            <div className="text-xs text-muted-foreground mb-1">Par raison de sortie</div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-1">Sortie</th>
                  <th className="text-right py-1 px-2">N</th>
                  <th className="text-right py-1 px-2">WR%</th>
                  <th className="text-right py-1 px-2">PnL total</th>
                </tr>
              </thead>
              <tbody>
                {stats.byExit.map(([exit, s]) => {
                  const badge = exitReasonBadge(exit);
                  return (
                    <tr key={exit} className="border-b border-border/50">
                      <td className="py-1">
                        <span
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[0.65rem] font-mono"
                          style={{ color: badge.color, background: `${badge.color}15`, border: `1px solid ${badge.color}33` }}
                        >
                          {badge.emoji} {badge.abbr}
                        </span>
                      </td>
                      <td className="text-right py-1 px-2 font-mono">{s.n}</td>
                      <td className="text-right py-1 px-2 font-mono">
                        {((s.wins / s.n) * 100).toFixed(1)}%
                      </td>
                      <td className={`text-right py-1 px-2 font-mono ${s.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
