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
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import type { TimeSeries } from '@/types';

// ── Helpers ─────────────────────────────────────────────────────────────────

const SYMBOLS = ['BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'BNB/USDC', 'XRP/USDC'];

/** Profondeur d'affichage (filtre client sur les timestamps). */
const DEPTH_OPTIONS: { id: string; label: string; days: number | null }[] = [
  { id: '1m', label: '1 mois', days: 30 },
  { id: '3m', label: '3 mois', days: 91 },
  { id: '6m', label: '6 mois', days: 182 },
  { id: '1y', label: '1 an', days: 365 },
  { id: '2y', label: '2 ans', days: 730 },
  { id: 'all', label: 'Tout', days: null },
];

function filterSeriesByDepth(
  series: TimeSeries | undefined,
  depthDays: number | null,
): TimeSeries | undefined {
  if (!series?.time?.length || depthDays == null) return series;
  const cutoff = Math.floor(Date.now() / 1000) - depthDays * 86400;
  const times = series.time as number[];
  const values = series.value as (number | null)[];
  const idx: number[] = [];
  for (let i = 0; i < times.length; i++) {
    if (Number(times[i]) >= cutoff) idx.push(i);
  }
  if (idx.length === times.length) return series;
  return {
    ...series,
    time: idx.map((i) => times[i]),
    value: idx.map((i) => values[i]),
    count: idx.length,
  };
}

function filterPriceByDepth(
  price: { time?: number[]; close?: number[] } | null,
  depthDays: number | null,
) {
  if (!price?.time?.length || depthDays == null) return price;
  const cutoff = Math.floor(Date.now() / 1000) - depthDays * 86400;
  const times = price.time;
  const close = price.close || [];
  const nt: number[] = [];
  const nc: number[] = [];
  for (let i = 0; i < times.length; i++) {
    if (Number(times[i]) >= cutoff) {
      nt.push(times[i]);
      nc.push(close[i]);
    }
  }
  return { time: nt, close: nc };
}

function timeToLabel(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  if (isNaN(d.getTime())) return '—';
  // jj/mm/aa (demande UI)
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const yy = String(d.getFullYear()).slice(-2);
  return `${dd}/${mm}/${yy}`;
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

interface MetricChartProps {
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
  /** Domaine Y prix partagé entre les 4 graphiques (même échelle). */
  priceDomain?: [number, number] | null;
  /** Domaine temps X partagé (même plage pour les 4). */
  xTickMin?: number | null;
  xTickMax?: number | null;
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
  priceDomain,
  xTickMin,
  xTickMax,
}: MetricChartProps) {
  let base = downsample(series?.time || [], series?.value || []);
  // Filtre X partagé (profondeur déjà appliquée côté parent, re-bornes si besoin)
  if (xTickMin != null || xTickMax != null) {
    base = base.filter((p) => {
      if (xTickMin != null && p.t < xTickMin) return false;
      if (xTickMax != null && p.t > xTickMax) return false;
      return true;
    });
  }
  const data = showPrice ? mergePrice(base, priceSeries || null) : base;
  const count = data.length || series?.count || 0;
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
                {/* Métrique à gauche ; prix OHLCV à droite (quand superposé) */}
                <YAxis
                  yAxisId="metric"
                  orientation="left"
                  stroke="#6b7280"
                  fontSize={9}
                  domain={['auto', 'auto']}
                  tickFormatter={(v) => (v == null ? '—' : `${Number(v).toFixed(3)}${unit}`)}
                />
                {showPrice && (
                  <YAxis
                    yAxisId="price"
                    orientation="right"
                    stroke="rgba(56,189,248,.85)"
                    fontSize={9}
                    domain={priceDomain || ['auto', 'auto']}
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
  const { defaultTf } = useTradingTimeframes('1h');
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [period, setPeriod] = useState(defaultTf);
  const [depth, setDepth] = useState('3m');
  const [refresh, setRefresh] = useState(false);
  const [showPrice, setShowPrice] = useState(false);

  const { data, isLoading, isError, isFetching, refetch } = useDerivativesData(symbol, period, refresh);
  const { data: statusData, isLoading: statusLoading } = useDerivativesStatus(symbol);

  const depthDays = DEPTH_OPTIONS.find((d) => d.id === depth)?.days ?? null;
  const rawMetrics = data?.metrics || {};
  const metrics: Record<string, TimeSeries | undefined> = {};
  for (const [k, v] of Object.entries(rawMetrics)) {
    metrics[k] = filterSeriesByDepth(v as TimeSeries, depthDays);
  }
  const priceData = filterPriceByDepth(data?.price || null, depthDays);
  const isEnabled = (statusData as any)?.enabled ?? (data as any)?.enabled ?? true;

  // Plage X partagée (min/max timestamps après filtre profondeur)
  const sharedX = (() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of Object.values(metrics)) {
      const times = (s as TimeSeries)?.time || [];
      for (const t of times) {
        const n = Number(t);
        if (Number.isFinite(n)) {
          if (n < lo) lo = n;
          if (n > hi) hi = n;
        }
      }
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { min: null as number | null, max: null as number | null, days: 0 };
    return { min: lo, max: hi, days: Math.max(0, (hi - lo) / 86400) };
  })();

  // Domaine Y prix partagé (même échelle droite sur les 4 charts)
  const sharedPriceDomain = (() => {
    if (!showPrice || !priceData?.close?.length) return null;
    const closes = priceData.close.filter((c) => c != null && Number.isFinite(Number(c))).map(Number);
    if (!closes.length) return null;
    const mn = Math.min(...closes);
    const mx = Math.max(...closes);
    const pad = (mx - mn) * 0.05 || mx * 0.01;
    return [mn - pad, mx + pad] as [number, number];
  })();

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
            <label className="text-xs text-dim block mb-1.5">Timeframe</label>
            <TimeframeButtons value={period} onChange={setPeriod} size="sm" />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Profondeur</label>
            <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Profondeur d'affichage">
              {DEPTH_OPTIONS.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  role="radio"
                  aria-checked={depth === d.id}
                  onClick={() => setDepth(d.id)}
                  className={
                    depth === d.id
                      ? 'px-2.5 py-1 rounded-md text-xs border bg-primary-500/15 text-primary-400 border-primary-500/40'
                      : 'px-2.5 py-1 rounded-md text-xs border bg-card-hover text-muted border-border hover:border-border-hi'
                  }
                >
                  {d.label}
                </button>
              ))}
            </div>
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

      {/* Info profondeur réelle (souvent ~2 mois si cache court) */}
      {!isLoading && !isError && sharedX.days > 0 && (
        <p className="text-[11px] text-dim">
          Plage affichée : ~{sharedX.days.toFixed(0)} j de données
          {depthDays != null && sharedX.days + 5 < depthDays
            ? ` (demandé ${DEPTH_OPTIONS.find((d) => d.id === depth)?.label} — cache plus court)`
            : ''}
          {showPrice && sharedPriceDomain
            ? ` · échelle prix partagée $${sharedPriceDomain[0].toFixed(0)}–$${sharedPriceDomain[1].toFixed(0)}`
            : ''}
        </p>
      )}

      {/* 4 metric charts — même plage X + prix Y partagé */}
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
            priceDomain={sharedPriceDomain}
            xTickMin={sharedX.min}
            xTickMax={sharedX.max}
          />
          <MetricChart
            title="Open Interest"
            series={metrics.open_interest}
            color="#8b5cf6"
            icon={<Coins className="w-3.5 h-3.5 text-purple-400" />}
            unit=""
            priceSeries={priceData}
            showPrice={showPrice}
            priceDomain={sharedPriceDomain}
            xTickMin={sharedX.min}
            xTickMax={sharedX.max}
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
            priceDomain={sharedPriceDomain}
            xTickMin={sharedX.min}
            xTickMax={sharedX.max}
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
            priceDomain={sharedPriceDomain}
            xTickMin={sharedX.min}
            xTickMax={sharedX.max}
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
