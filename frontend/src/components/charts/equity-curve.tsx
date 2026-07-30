'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from 'recharts';
import { useBotStatus, useDailyStats } from '@/hooks/use-api';
import { useMemo } from 'react';
import { formatUSD } from '@/lib/utils';

interface DailyStat {
  date: string;
  trades: number;
  wins: number;
  pnl: number;
  fees: number;
  max_dd: number;
  equity_open: number;
  equity_close: number;
}

/**
 * S0-F1-US1 — EquityCurve réelle.
 *
 * Avant : le composant affichait des données sin/cos simulées (TODO mentionnait
 * `/api/stats/daily` non câblé). Le KPI le plus regardé par un trader était
 * donc trompeur.
 *
 * Maintenant : consomme `useDailyStats(30)` qui tape sur `/api/stats/daily`.
 * Construit la courbe d'équité à partir des `equity_close` journaliers. Si
 * aucune donnée n'est disponible (bot tout juste démarré, jamais tradeé),
 * affiche un EmptyState explicite plutôt qu'une simulation trompeuse.
 */
export function EquityCurve() {
  const { data: status } = useBotStatus();
  const { data: dailyStats, isLoading, isError } = useDailyStats(30);

  const chartData = useMemo(() => {
    if (!dailyStats || !Array.isArray(dailyStats) || dailyStats.length === 0) return [];
    // Les stats sont généralement triées par date croissante côté backend.
    // On les remappe pour recharts.
    return dailyStats.map((d: DailyStat) => ({
      time: d.date,
      equity: Number(d.equity_close ?? d.equity_open ?? 0),
      pnl: Number(d.pnl ?? 0),
    }));
  }, [dailyStats]);

  // Si pas d'historique mais qu'on a un status live, on affiche au moins le
  // point courant (capital + PnL) — pas une simulation.
  const fallbackData = useMemo(() => {
    if (chartData.length > 0 || !status) return [];
    const currentEquity = (status.capital ?? 0) + (status.total_pnl ?? 0);
    return [{
      time: new Date().toISOString().slice(0, 10),
      equity: currentEquity,
      pnl: status.total_pnl ?? 0,
    }];
  }, [chartData, status]);

  const data = chartData.length > 0 ? chartData : fallbackData;
  const currentEquity = data[data.length - 1]?.equity ?? 0;
  const startEquity = data[0]?.equity ?? status?.capital ?? 0;
  const delta = currentEquity - startEquity;
  const deltaPct = startEquity > 0 ? (delta / startEquity) * 100 : 0;
  const color = delta >= 0 ? '#10b981' : '#ef4444';

  const hasRealData = chartData.length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Equity Curve</span>
          {!hasRealData && (
            <span className="text-[10px] font-normal text-muted uppercase tracking-wider">
              En attente de données
            </span>
          )}
        </CardTitle>
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm font-semibold">
            ${currentEquity.toFixed(2)}
          </span>
          {data.length > 1 && (
            <span className="text-xs font-mono" style={{ color }}>
              {delta >= 0 ? '+' : ''}{delta.toFixed(2)} ({deltaPct.toFixed(2)}%)
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="h-64 w-full flex items-center justify-center text-muted text-sm">
            Chargement...
          </div>
        ) : isError ? (
          <div className="h-64 w-full flex items-center justify-center text-red-400 text-sm">
            Erreur de chargement de l&apos;historique
          </div>
        ) : data.length === 0 ? (
          <div className="h-64 w-full flex flex-col items-center justify-center gap-2 text-muted text-sm">
            <span className="text-dim">Pas encore de trades</span>
            <span className="text-xs">Lancez un backtest ou démarrez le bot pour voir l&apos;equity curve</span>
          </div>
        ) : (
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={color} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                <XAxis
                  dataKey="time"
                  stroke="#6b7280"
                  fontSize={10}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  stroke="#6b7280"
                  fontSize={10}
                  tickLine={false}
                  axisLine={false}
                  domain={['auto', 'auto']}
                  tickFormatter={(v) => `$${v.toFixed(0)}`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#141a23',
                    border: '1px solid #1f2937',
                    borderRadius: '8px',
                    fontSize: '12px',
                  }}
                  labelStyle={{ color: '#9ca3af' }}
                  formatter={(value: any) => [formatUSD(Number(value)), 'Equity']}
                />
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke={color}
                  strokeWidth={2}
                  fill="url(#equityGradient)"
                  animationDuration={500}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
