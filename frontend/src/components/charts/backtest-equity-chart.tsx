'use client';

/**
 * E3-F2-US6 — Courbe d'équité du backtest avec Buy & Hold superposé.
 *
 * `/lab` n'affichait AUCUNE courbe d'équité pour un résultat de backtest (juste
 * des cartes de KPI), alors que le backend renvoie déjà tout le nécessaire par
 * stratégie (app/api/routes/backtest.py, dict `entry`) :
 *   { equity_curve: number[], initial_capital, final_equity,
 *     buy_and_hold_pnl, buy_and_hold_pct, alpha, ... }
 *
 * `equity_curve` est une série de capitaux SANS horodatage (un point par trade
 * clôturé, poussé dans `ctx.equity_curve`) — l'axe X est donc l'index de trade,
 * pas une date. Le Buy & Hold n'existe que comme SCALAIRE (`buy_and_hold_pnl`) :
 * il est tracé en droite du capital initial vers capital + B&H, ce qui est
 * exactement sa définition (position ouverte au début, tenue jusqu'au bout), et
 * non une série reconstituée qu'on aurait inventée.
 */

import { useMemo } from 'react';
import {
  Area, AreaChart, CartesianGrid, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
  ComposedChart, ReferenceLine,
} from 'recharts';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ChartFullscreen } from '@/components/ui/chart-fullscreen';
import { TrendingUp } from 'lucide-react';
import { cn, formatUSD } from '@/lib/utils';

export interface BacktestEquityProps {
  equityCurve?: number[];
  initialCapital?: number;
  buyAndHoldPnl?: number | null;
  alpha?: number | null;
  strategy?: string;
}

export function BacktestEquityChart({
  equityCurve,
  initialCapital,
  buyAndHoldPnl,
  alpha,
  strategy,
}: BacktestEquityProps) {
  const capital = Number(initialCapital ?? 0);
  const bh = buyAndHoldPnl === null || buyAndHoldPnl === undefined ? null : Number(buyAndHoldPnl);

  const data = useMemo(() => {
    const curve = Array.isArray(equityCurve) ? equityCurve : [];
    if (curve.length === 0) return [];
    // Point 0 = capital initial, pour que la courbe parte du bon niveau : le
    // backend ne pousse un point qu'à la CLÔTURE de chaque trade.
    const pts = [{ i: 0, equity: capital || Number(curve[0] ?? 0), bh: capital || Number(curve[0] ?? 0) }];
    const n = curve.length;
    for (let k = 0; k < n; k++) {
      pts.push({
        i: k + 1,
        equity: Number(curve[k] ?? 0),
        // Interpolation linéaire du Buy & Hold : seul son PnL total est connu.
        bh: bh === null ? NaN : capital + (bh * (k + 1)) / n,
      });
    }
    return pts;
  }, [equityCurve, capital, bh]);

  if (data.length < 2) return null;

  const final = data[data.length - 1].equity;
  const pnl = final - capital;
  const alphaNum = alpha === null || alpha === undefined ? null : Number(alpha);

  const chart = ({ fullscreen }: { fullscreen: boolean }) => (
    <div className={fullscreen ? 'h-full' : 'h-72'}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
          <defs>
            <linearGradient id="btEquityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#06b6d4" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#06b6d4" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="i"
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            label={{ value: 'Trades clôturés', position: 'insideBottom', offset: -4, fontSize: 10, fill: '#94a3b8' }}
          />
          <YAxis
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => formatUSD(v)}
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            width={80}
          />
          <Tooltip
            contentStyle={{
              background: '#141a23',
              border: '1px solid #1f2937',
              borderRadius: 8,
              fontSize: 11,
            }}
            formatter={(v: number, name: string) => [formatUSD(Number(v)), name]}
            labelFormatter={(l) => `Trade ${l}`}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {capital > 0 && <ReferenceLine y={capital} stroke="#6b7280" strokeDasharray="4 4" />}
          <Area
            type="monotone"
            dataKey="equity"
            name="Équité stratégie"
            stroke="#06b6d4"
            strokeWidth={2}
            fill="url(#btEquityFill)"
          />
          {bh !== null && (
            <Line
              type="monotone"
              dataKey="bh"
              name="Buy & Hold"
              stroke="#f59e0b"
              strokeWidth={1.5}
              strokeDasharray="5 4"
              dot={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <TrendingUp className="w-3.5 h-3.5" />
          Courbe d&apos;équité{strategy ? ` — ${strategy}` : ''}
        </CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant={pnl >= 0 ? 'success' : 'danger'}>
            {pnl >= 0 ? '+' : ''}{formatUSD(pnl)}
          </Badge>
          {alphaNum !== null && (
            <Badge variant={alphaNum >= 0 ? 'success' : 'muted'} title="Écart de performance vs Buy & Hold">
              α {alphaNum >= 0 ? '+' : ''}{formatUSD(alphaNum)}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <ChartFullscreen title={`Courbe d'équité${strategy ? ` — ${strategy}` : ''}`}>
          {chart}
        </ChartFullscreen>
        {bh !== null && (
          <p className={cn('mt-2 text-[10px]', (alphaNum ?? 0) >= 0 ? 'text-emerald-400' : 'text-amber-400')}>
            {(alphaNum ?? 0) >= 0
              ? 'La stratégie bat le Buy & Hold sur la période.'
              : 'Le Buy & Hold fait mieux que la stratégie sur la période.'}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
