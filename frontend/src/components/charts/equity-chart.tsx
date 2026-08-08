/**
 * F3 — EquityChart unifié (factorisation des 2 charts equity).
 *
 * Avant : `equity-curve.tsx` (live, /portfolio) et `backtest-equity-chart.tsx`
 * (backtest, /lab) avaient ~80% de code commun (AreaChart recharts, gradient,
 * CartesianGrid, formatUSD, tooltip style identique) mais ne partageaient
 * rien. Toute correction (ex : contraste, formatage, gestion NaN) devait
 * être appliquée 2 fois.
 *
 * Ce composant propose une API unique avec deux variantes :
 *
 *   <EquityChart variant="live" />            → consomme /api/stats/daily
 *   <EquityChart variant="backtest"           → consomme les props
 *     equityCurve={...} initialCapital={...}
 *     buyAndHoldPnl={...} alpha={...} />
 *
 * Migration progressive : les composants existants (`equity-curve.tsx`,
 * `backtest-equity-chart.tsx`) restent disponibles pour ne pas casser les
 * imports actuels. Ils pourront être migrés vers `EquityChart` dans les
 * prochaines itérations.
 */

'use client';

import { useMemo } from 'react';
import {
  Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
  ComposedChart, ReferenceLine,
} from 'recharts';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { ChartFullscreen } from '@/components/ui/chart-fullscreen';
import { formatUSD } from '@/lib/utils';

export type EquityChartVariant = 'live' | 'backtest';

export interface EquityChartProps {
  variant: EquityChartVariant;

  // ── Props pour variant="live" ─────────────────────────────────────────────
  /** Données journalières (depuis /api/stats/daily). Format : [{date, equity_close, pnl}]. */
  dailyData?: Array<{ date: string; equity_close: number; equity_open: number; pnl: number }>;
  /** Capital courant (pour fallback si pas d'historique). */
  currentCapital?: number;
  currentPnl?: number;
  isLoading?: boolean;
  isError?: boolean;

  // ── Props pour variant="backtest" ─────────────────────────────────────────
  equityCurve?: number[];
  initialCapital?: number;
  buyAndHoldPnl?: number | null;
  alpha?: number | null;
  strategy?: string;

  // ── Props communes ─────────────────────────────────────────────────────────
  height?: number;
  showFullscreen?: boolean;
  title?: string;
}

export function EquityChart({
  variant,
  dailyData,
  currentCapital,
  currentPnl,
  isLoading,
  isError,
  equityCurve,
  initialCapital,
  buyAndHoldPnl,
  alpha,
  strategy,
  height = 256,
  showFullscreen = false,
  title,
}: EquityChartProps) {
  // ── Variant "live" : prépare les données depuis /api/stats/daily ──────────
  const liveData = useMemo(() => {
    if (variant !== 'live') return [];
    if (!dailyData || !Array.isArray(dailyData) || dailyData.length === 0) return [];
    return dailyData.map((d) => ({
      time: d.date,
      equity: Number(d.equity_close ?? d.equity_open ?? 0),
      pnl: Number(d.pnl ?? 0),
    }));
  }, [dailyData, variant]);

  const liveFallback = useMemo(() => {
    if (variant !== 'live' || liveData.length > 0) return [];
    if (currentCapital == null) return [];
    return [{
      time: new Date().toISOString().slice(0, 10),
      equity: currentCapital + (currentPnl ?? 0),
      pnl: currentPnl ?? 0,
    }];
  }, [liveData, currentCapital, currentPnl, variant]);

  const liveSeries = liveData.length > 0 ? liveData : liveFallback;

  // ── Variant "backtest" : prépare les données depuis equity_curve + B&H ─────
  const backtestData = useMemo(() => {
    if (variant !== 'backtest') return [];
    const curve = Array.isArray(equityCurve) ? equityCurve : [];
    if (curve.length === 0) return [];
    const capital = Number(initialCapital ?? 0);
    const bh = buyAndHoldPnl === null || buyAndHoldPnl === undefined
      ? null : Number(buyAndHoldPnl);
    // Point 0 = capital initial
    const pts = [{
      i: 0,
      equity: capital || Number(curve[0] ?? 0),
      bh: capital || Number(curve[0] ?? 0),
    }];
    const n = curve.length;
    for (let k = 0; k < n; k++) {
      pts.push({
        i: k + 1,
        equity: Number(curve[k] ?? 0),
        // Interpolation linéaire du Buy & Hold : seul son PnL total est connu.
        bh: bh != null
          ? capital + (bh * (k + 1) / n)
          : capital,
      });
    }
    return pts;
  }, [equityCurve, initialCapital, buyAndHoldPnl, variant]);

  // ── Couleurs et métriques communes ────────────────────────────────────────
  const color = useMemo(() => {
    if (variant === 'live') {
      const data = liveSeries;
      if (data.length < 2) return '#10b981';
      const start = data[0].equity;
      const end = data[data.length - 1].equity;
      return end >= start ? '#10b981' : '#ef4444';
    }
    // backtest
    const data = backtestData;
    if (data.length < 2) return '#10b981';
    const start = data[0].equity;
    const end = data[data.length - 1].equity;
    return end >= start ? '#10b981' : '#ef4444';
  }, [variant, liveSeries, backtestData]);

  const displayTitle = title ?? (variant === 'live' ? 'Equity Curve' : `Equity — ${strategy ?? 'backtest'}`);

  // ── États vides / loading / error (variant live seulement) ────────────────
  if (variant === 'live' && isLoading) {
    return (
      <Card>
        <CardHeader><CardTitle>{displayTitle}</CardTitle></CardHeader>
        <CardContent>
          <div className="w-full flex items-center justify-center text-muted text-sm"
               style={{ height }}>
            Chargement...
          </div>
        </CardContent>
      </Card>
    );
  }
  if (variant === 'live' && isError) {
    return (
      <Card>
        <CardHeader><CardTitle>{displayTitle}</CardTitle></CardHeader>
        <CardContent>
          <div className="w-full flex items-center justify-center text-red-400 text-sm"
               style={{ height }}>
            Erreur de chargement de l&apos;historique
          </div>
        </CardContent>
      </Card>
    );
  }
  if (variant === 'live' && liveSeries.length === 0) {
    return (
      <Card>
        <CardHeader><CardTitle>{displayTitle}</CardTitle></CardHeader>
        <CardContent>
          <div className="w-full flex flex-col items-center justify-center gap-2 text-muted text-sm"
               style={{ height }}>
            <span className="text-dim">Pas encore de trades</span>
            <span className="text-xs">
              Lancez un backtest ou démarrez le bot pour voir l&apos;equity curve
            </span>
          </div>
        </CardContent>
      </Card>
    );
  }
  if (variant === 'backtest' && backtestData.length === 0) {
    return null;
  }

  // ── Rendu du chart ────────────────────────────────────────────────────────
  const renderChart = (h: number) => variant === 'live' ? (
    <ResponsiveContainer width="100%" height={h}>
      <AreaChart data={liveSeries} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="equityGradientLive" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
        <XAxis dataKey="time" stroke="#6b7280" fontSize={10} tickLine={false} axisLine={false} />
        <YAxis stroke="#6b7280" fontSize={10} tickLine={false} axisLine={false}
               domain={['auto', 'auto']} tickFormatter={(v) => `$${v.toFixed(0)}`} />
        <Tooltip
          contentStyle={{
            backgroundColor: '#141a23', border: '1px solid #1f2937',
            borderRadius: '8px', fontSize: '12px',
          }}
          labelStyle={{ color: '#9ca3af' }}
          formatter={(value: number) => [formatUSD(Number(value)), 'Equity']}
        />
        <Area type="monotone" dataKey="equity" stroke={color} strokeWidth={2}
              fill="url(#equityGradientLive)" animationDuration={500} />
      </AreaChart>
    </ResponsiveContainer>
  ) : (
    <ResponsiveContainer width="100%" height={h}>
      <ComposedChart data={backtestData} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="equityGradientBt" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
        <XAxis dataKey="i" stroke="#6b7280" fontSize={10} tickLine={false} axisLine={false}
               label={{ value: 'Trade #', position: 'insideBottom', offset: -2, fontSize: 10, fill: '#6b7280' }} />
        <YAxis stroke="#6b7280" fontSize={10} tickLine={false} axisLine={false}
               domain={['auto', 'auto']} tickFormatter={(v) => `$${v.toFixed(0)}`} />
        <Tooltip
          contentStyle={{
            backgroundColor: '#141a23', border: '1px solid #1f2937',
            borderRadius: '8px', fontSize: '12px',
          }}
          labelStyle={{ color: '#9ca3af' }}
          formatter={(value: number, name: string) => [
            formatUSD(Number(value)),
            name === 'equity' ? 'Equity' : name === 'bh' ? 'Buy & Hold' : name,
          ]}
        />
        <ReferenceLine y={Number(initialCapital ?? 0)} stroke="#6b7280"
                       strokeDasharray="3 3" fontSize={9} />
        <Area type="monotone" dataKey="equity" stroke={color} strokeWidth={2}
              fill="url(#equityGradientBt)" animationDuration={500} />
        <Line type="monotone" dataKey="bh" stroke="#9ca3af" strokeWidth={1}
              strokeDasharray="4 2" dot={false} animationDuration={500} />
      </ComposedChart>
    </ResponsiveContainer>
  );

  const chart = renderChart(height);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>{displayTitle}</span>
          {variant === 'live' && liveData.length === 0 && liveFallback.length > 0 && (
            <span className="text-[10px] font-normal text-muted uppercase tracking-wider">
              En attente de données
            </span>
          )}
          {showFullscreen && (
            <ChartFullscreen title={displayTitle}>
              {({ fullscreen }) => (
                <div style={{ height: fullscreen ? 480 : height }}>
                  {/* Re-render du chart à la nouvelle hauteur */}
                  {fullscreen ? renderChart(480) : chart}
                </div>
              )}
            </ChartFullscreen>
          )}
        </CardTitle>
        {variant === 'live' && liveSeries.length > 0 && (
          <EquityDelta equity={liveSeries[liveSeries.length - 1].equity}
                        startEquity={liveSeries[0].equity} />
        )}
        {variant === 'backtest' && backtestData.length > 0 && (
          <EquityDelta equity={backtestData[backtestData.length - 1].equity}
                        startEquity={backtestData[0].equity}
                        alpha={alpha} />
        )}
      </CardHeader>
      <CardContent>
        {chart}
      </CardContent>
    </Card>
  );
}

function EquityDelta({
  equity, startEquity, alpha,
}: { equity: number; startEquity: number; alpha?: number | null }) {
  const delta = equity - startEquity;
  const deltaPct = startEquity > 0 ? (delta / startEquity) * 100 : 0;
  const color = delta >= 0 ? '#10b981' : '#ef4444';
  return (
    <div className="flex items-center gap-3">
      <span className="font-mono text-sm font-semibold">${equity.toFixed(2)}</span>
      <span className="text-xs font-mono" style={{ color }}>
        {delta >= 0 ? '+' : ''}{delta.toFixed(2)} ({deltaPct.toFixed(2)}%)
      </span>
      {alpha != null && Number.isFinite(alpha) && (
        <span className="text-xs font-mono text-muted-foreground">
          α {alpha >= 0 ? '+' : ''}{alpha.toFixed(2)}
        </span>
      )}
    </div>
  );
}
