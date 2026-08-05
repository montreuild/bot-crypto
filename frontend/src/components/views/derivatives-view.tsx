'use client';

/**
 * Onglet Dérivés de `/market` — funding, OI, long/short et taker ratios.
 *
 * Lot Marché : cette vue était la page `/derivatives`, vers laquelle l'onglet
 * se contentait de renvoyer par une `RedirectCard`. `/derivatives` est
 * désormais en 308 vers `/market?tab=derivatives`.
 */

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { formatDateTime } from '@/lib/utils';
import { toast } from 'sonner';
import { useDerivativesData, useDerivativesStatus } from '@/hooks/use-api';
import {
  Loader2, RefreshCw, AlertCircle, Activity, Coins, Scale, BarChart2,
} from 'lucide-react';
import {
  LineChart, Line, ResponsiveContainer, XAxis, YAxis,
  CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts';
import type { TimeSeries } from '@/types';

// ── Helpers ─────────────────────────────────────────────────────────────────

const SYMBOLS = ['BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'BNB/USDC', 'XRP/USDC'];
const PERIODS = ['15m', '1h', '4h', '1d'];

function timeToLabel(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function downsample(times: number[], values: (number | null)[], max = 400) {
  if (!times || times.length === 0) return [];
  const step = times.length > max ? Math.ceil(times.length / max) : 1;
  const out: Array<{ time: string; value: number | null; t: number }> = [];
  for (let i = 0; i < times.length; i += step) {
    out.push({ time: timeToLabel(times[i]), value: values[i] ?? null, t: times[i] });
  }
  return out;
}

/** Aligne le prix (close) sur les timestamps de la métrique pour dual-axis. */
function mergePrice(
  metric: Array<{ time: string; value: number | null; t: number }>,
  price: { time?: number[]; close?: number[] } | null,
) {
  if (!price?.time?.length || !price.close?.length) return metric.map((m) => ({ ...m, price: null as number | null }));
  const pt = price.time;
  const pc = price.close;
  let j = 0;
  return metric.map((m) => {
    while (j < pt.length - 1 && pt[j + 1] <= m.t) j += 1;
    // nearest
    let best = j;
    if (j + 1 < pt.length && Math.abs(pt[j + 1] - m.t) < Math.abs(pt[j] - m.t)) best = j + 1;
    return { ...m, price: pc[best] ?? null };
  });
}

// ── Chart sub-component ─────────────────────────────────────────────────────

interface ChartProps {
  title: string;
  series?: TimeSeries;
  color: string;
  icon: React.ReactNode;
  unit?: string;
  referenceValue?: number;
  referenceLabel?: string;
  /** Série prix close pour overlay (dual Y-axis, style Jinja2). */
  priceSeries?: { time?: number[]; close?: number[] } | null;
  showPrice?: boolean;
}

function MetricChart({
  title,
  series,
  color,
  icon,
  unit = '',
  referenceValue,
  referenceLabel,
  priceSeries,
  showPrice,
}: ChartProps) {
  const base = downsample(series?.time || [], series?.value || []);
  const data = showPrice ? mergePrice(base, priceSeries || null) : base;
  const count = series?.count ?? 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {icon}
          {title}
        </CardTitle>
        <Badge variant="default">{count} pts</Badge>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <div className="h-44 flex items-center justify-center text-sm text-muted">
            Aucune donnée
          </div>
        ) : (
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                <XAxis dataKey="time" stroke="#6b7280" fontSize={9} minTickGap={40} />
                <YAxis
                  yAxisId="metric"
                  stroke="#6b7280"
                  fontSize={9}
                  domain={['auto', 'auto']}
                  tickFormatter={(v) => (v == null ? '—' : `${Number(v).toFixed(3)}${unit}`)}
                />
                {showPrice && (
                  <YAxis
                    yAxisId="price"
                    orientation="left"
                    stroke="rgba(221,230,245,.45)"
                    fontSize={9}
                    domain={['auto', 'auto']}
                    tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
                  />
                )}
                <Tooltip
                  contentStyle={{ backgroundColor: '#141a23', border: '1px solid #1f2937', borderRadius: '8px' }}
                  formatter={(v: any, name: string) => {
                    if (v == null) return ['—', name];
                    if (name === 'price') return [`$${Number(v).toFixed(2)}`, 'Prix'];
                    return [`${Number(v).toFixed(5)}${unit}`, title];
                  }}
                />
                {referenceValue != null && (
                  <ReferenceLine
                    yAxisId="metric"
                    y={referenceValue}
                    stroke="#f59e0b"
                    strokeDasharray="4 4"
                    label={{ value: referenceLabel || '', fill: '#f59e0b', fontSize: 10 }}
                  />
                )}
                {showPrice && (
                  <Line
                    yAxisId="price"
                    type="monotone"
                    dataKey="price"
                    stroke="rgba(221,230,245,.35)"
                    strokeWidth={1}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                )}
                <Line
                  yAxisId="metric"
                  type="monotone"
                  dataKey="value"
                  stroke={color}
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Status table ────────────────────────────────────────────────────────────

function StatusTable({ status }: { status: any }) {
  const metrics = status?.metrics || status?.status || status;
  const entries: Array<[string, any]> = [];
  if (metrics && typeof metrics === 'object') {
    for (const [k, v] of Object.entries(metrics)) {
      if (v && typeof v === 'object') {
        entries.push([k, v]);
      }
    }
  }
  if (entries.length === 0) {
    return <div className="text-sm text-muted text-center py-4">Pas de status disponible</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-dim border-b border-border">
            <th className="p-3 font-medium">Métrique</th>
            <th className="p-3 font-medium text-right">Points</th>
            <th className="p-3 font-medium">Premier</th>
            <th className="p-3 font-medium">Dernier</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, info]: [string, any]) => (
            <tr key={name} className="border-b border-border/30 hover:bg-card-hover">
              <td className="p-3 font-mono">{name}</td>
              <td className="p-3 text-right font-mono text-muted">{info?.count ?? 0}</td>
              <td className="p-3 text-xs text-muted font-mono">{formatDateTime(info?.first)}</td>
              <td className="p-3 text-xs text-muted font-mono">{formatDateTime(info?.last)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Vue ─────────────────────────────────────────────────────────────────────

export function DerivativesView() {
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [period, setPeriod] = useState('1h');
  const [refresh, setRefresh] = useState(false);
  const [showPrice, setShowPrice] = useState(false);

  const { data, isLoading, isError, isFetching, refetch } = useDerivativesData(symbol, period, refresh);
  const { data: statusData, isLoading: statusLoading } = useDerivativesStatus(symbol);

  const metrics = data?.metrics || {};
  const priceData = data?.price || null;
  const isEnabled = (statusData as any)?.enabled ?? (data as any)?.enabled ?? true;

  const handleForceRefresh = async () => {
    setRefresh(true);
    try {
      await refetch();
      toast.success('Rafraîchi (network)');
    } finally {
      // Reset refresh flag after a short delay to allow query to re-run
      setTimeout(() => setRefresh(false), 500);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Données dérivées</h2>
          <p className="text-sm text-muted mt-1">
            Funding rate · Open Interest · Long/Short Ratio · Taker Buy/Sell Ratio
          </p>
        </div>
        <Badge variant={isEnabled ? 'success' : 'danger'}>
          {isEnabled ? 'Activé' : 'Désactivé'}
        </Badge>
      </div>

      {/* Controls */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div>
            <label className="text-xs text-dim block mb-1.5">Symbole</label>
            <select
              aria-label="Symbole"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            >
              {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Période</label>
            <select
              aria-label="Période"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            >
              {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer h-10">
            <input
              type="checkbox"
              checked={showPrice}
              onChange={(e) => setShowPrice(e.target.checked)}
              className="rounded"
            />
            Prix OHLCV superposé
          </label>
          <div className="flex-1" />
          <Button
            variant="primary"
            onClick={handleForceRefresh}
            disabled={isFetching}
          >
            {isFetching ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Refresh (network)
          </Button>
        </CardContent>
      </Card>

      {/* Loading / error state */}
      {isLoading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
        </div>
      )}
      {isError && (
        <Card>
          <CardContent className="text-center py-8 text-red-400 text-sm">
            <AlertCircle className="w-8 h-8 mx-auto mb-2" />
            Erreur lors du chargement des données dérivées
          </CardContent>
        </Card>
      )}

      {/* 4 metric charts — overlay prix sur chaque chart (parité Jinja2) */}
      {!isLoading && !isError && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <MetricChart
            title="Funding Rate"
            series={metrics.funding_rate}
            color="#f59e0b"
            icon={<Activity className="w-3.5 h-3.5 text-amber-400" />}
            unit=""
            referenceValue={0}
            referenceLabel="0"
            priceSeries={priceData}
            showPrice={showPrice}
          />
          <MetricChart
            title="Open Interest"
            series={metrics.open_interest}
            color="#8b5cf6"
            icon={<Coins className="w-3.5 h-3.5 text-purple-400" />}
            unit=""
            priceSeries={priceData}
            showPrice={showPrice}
          />
          <MetricChart
            title="Long/Short Ratio"
            series={metrics.long_short_ratio}
            color="#22d3ee"
            icon={<Scale className="w-3.5 h-3.5 text-cyan-400" />}
            unit=""
            referenceValue={1}
            referenceLabel="1.0"
            priceSeries={priceData}
            showPrice={showPrice}
          />
          <MetricChart
            title="Taker Buy/Sell Ratio"
            series={metrics.taker_buy_sell_ratio}
            color="#10b981"
            icon={<BarChart2 className="w-3.5 h-3.5 text-emerald-400" />}
            unit=""
            referenceValue={1}
            referenceLabel="1.0"
            priceSeries={priceData}
            showPrice={showPrice}
          />
        </div>
      )}

      {/* Status section */}
      <Card>
        <CardHeader>
          <CardTitle>Status ({symbol})</CardTitle>
          <Badge variant="default">par métrique</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {statusLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 text-primary-400 animate-spin" />
            </div>
          ) : (
            <StatusTable status={statusData} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
