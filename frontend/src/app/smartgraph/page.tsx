'use client';

import { useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatTime, formatDateTime } from '@/lib/utils';
import { useSMC } from '@/hooks/use-api';
import { toast } from 'sonner';
import {
  Loader2, RefreshCw, AlertCircle, Activity,
  ArrowUp, ArrowDown, Target, Shield, Layers, Droplets, Waves, GitBranch, Sparkles,
} from 'lucide-react';
import {
  createChart, ColorType, LineStyle,
  type IChartApi, type ISeriesApi, type UTCTimestamp, type Time, type SeriesMarker,
} from 'lightweight-charts';

const TIMEFRAMES = ['15m', '30m', '1h', '4h', '1d'] as const;

interface OverlayToggles {
  orderBlocks: boolean;
  liquidityPools: boolean;
  fvg: boolean;
  trendlines: boolean;
  structure: boolean;
  premiumDiscount: boolean;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

interface CandleRow {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}

function cleanOhlcv(
  time: number[],
  open: number[],
  high: number[],
  low: number[],
  close: number[],
): CandleRow[] {
  const seen = new Set<number>();
  const out: CandleRow[] = [];
  for (let i = 0; i < time.length; i++) {
    const t = time[i];
    if (!Number.isFinite(t)) continue;
    if (seen.has(t)) continue;
    if (out.length > 0 && t < (out[out.length - 1].time as number)) continue;
    seen.add(t);
    out.push({
      time: t as UTCTimestamp,
      open: open[i],
      high: high[i],
      low: low[i],
      close: close[i],
    });
  }
  return out;
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function SmartGraphPage() {
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState<string>('1h');
  const [toggles, setToggles] = useState<OverlayToggles>({
    orderBlocks: true,
    liquidityPools: true,
    fvg: true,
    trendlines: true,
    structure: true,
    premiumDiscount: true,
  });

  const { data, isLoading, isError, isFetching, refetch, error } = useSMC(symbol, timeframe, 1000);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlaysRef = useRef<ISeriesApi<any>[]>([]);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0f1419' },
        textColor: '#9ca3af',
        fontFamily: 'var(--font-jetbrains), monospace',
      },
      grid: {
        vertLines: { color: '#1f2937' },
        horzLines: { color: '#1f2937' },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#1f2937' },
    });
    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });
    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      overlaysRef.current = [];
      priceLinesRef.current = [];
    };
  }, []);

  // Update candlestick data
  useEffect(() => {
    if (!candleSeriesRef.current || !data?.ohlcv) return;
    const ohlcv = data.ohlcv;
    const cleaned = cleanOhlcv(ohlcv.time || [], ohlcv.open || [], ohlcv.high || [], ohlcv.low || [], ohlcv.close || []);
    candleSeriesRef.current.setData(cleaned);
    chartRef.current?.timeScale().fitContent();
  }, [data?.ohlcv]);

  // Update overlays based on toggles
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries || !data) return;

    // Remove old overlays
    for (const s of overlaysRef.current) {
      try { chart.removeSeries(s); } catch { /* noop */ }
    }
    overlaysRef.current = [];
    // Remove old price lines
    for (const pl of priceLinesRef.current) {
      try { candleSeries.removePriceLine(pl); } catch { /* noop */ }
    }
    priceLinesRef.current = [];

    const ohlcvTime: number[] = data.ohlcv?.time || [];
    if (ohlcvTime.length === 0) {
      candleSeries.setMarkers([]);
      return;
    }
    const lastTime = ohlcvTime[ohlcvTime.length - 1] as UTCTimestamp;

    // Order Blocks — 2 horizontal line series per OB (top + bottom edges)
    if (toggles.orderBlocks && Array.isArray(data.order_blocks)) {
      for (const ob of data.order_blocks) {
        const bullish = ob.kind === 'bullish';
        const color = bullish ? 'rgba(16, 185, 129, 0.7)' : 'rgba(239, 68, 68, 0.7)';
        const ts = (ob.time_start ?? ohlcvTime[0]) as UTCTimestamp;
        const te = (ob.time_end ?? lastTime) as UTCTimestamp;
        if (te < ts) continue;
        const topSeries = chart.addLineSeries({
          color,
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        topSeries.setData([
          { time: ts, value: ob.top },
          { time: te, value: ob.top },
        ]);
        const botSeries = chart.addLineSeries({
          color,
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        botSeries.setData([
          { time: ts, value: ob.bottom },
          { time: te, value: ob.bottom },
        ]);
        overlaysRef.current.push(topSeries, botSeries);
      }
    }

    // FVG — dotted rectangle outline
    if (toggles.fvg && Array.isArray(data.fvgs)) {
      for (const f of data.fvgs) {
        const bullish = f.kind === 'bullish';
        const color = bullish ? 'rgba(34, 211, 238, 0.6)' : 'rgba(245, 158, 11, 0.6)';
        const ts = (f.time_start ?? ohlcvTime[0]) as UTCTimestamp;
        const te = (f.time_end ?? lastTime) as UTCTimestamp;
        if (te < ts) continue;
        const topSeries = chart.addLineSeries({
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        topSeries.setData([{ time: ts, value: f.top }, { time: te, value: f.top }]);
        const botSeries = chart.addLineSeries({
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        botSeries.setData([{ time: ts, value: f.bottom }, { time: te, value: f.bottom }]);
        overlaysRef.current.push(topSeries, botSeries);
      }
    }

    // Trendlines — diagonal dashed
    if (toggles.trendlines && Array.isArray(data.trendlines)) {
      for (const tl of data.trendlines) {
        const color = tl.kind === 'support' ? 'rgba(16, 185, 129, 0.9)' : 'rgba(239, 68, 68, 0.9)';
        const lineSeries = chart.addLineSeries({
          color,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        lineSeries.setData([
          { time: tl.start_time as UTCTimestamp, value: tl.start_price },
          { time: tl.end_time as UTCTimestamp, value: tl.end_price },
        ]);
        overlaysRef.current.push(lineSeries);
      }
    }

    // Liquidity Pools — horizontal price lines
    if (toggles.liquidityPools && Array.isArray(data.liquidity_pools)) {
      for (const lp of data.liquidity_pools) {
        const buyside = lp.kind === 'buyside';
        const color = buyside ? 'rgba(16, 185, 129, 0.85)' : 'rgba(239, 68, 68, 0.85)';
        const pl = candleSeries.createPriceLine({
          price: lp.level,
          color,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: `LP ${lp.kind}${lp.status === 'swept' ? ' ✕' : ''}`,
        });
        priceLinesRef.current.push(pl);
      }
    }

    // Premium / Discount — horizontal price lines
    if (toggles.premiumDiscount && data.premium_discount) {
      const pd = data.premium_discount;
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: pd.premium_top,
          color: 'rgba(239, 68, 68, 0.8)',
          lineStyle: LineStyle.Dotted,
          lineWidth: 1,
          axisLabelVisible: true,
          title: 'Premium',
        }),
        candleSeries.createPriceLine({
          price: pd.equilibrium,
          color: 'rgba(156, 163, 175, 0.8)',
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: 'Equilibrium',
        }),
        candleSeries.createPriceLine({
          price: pd.discount_bottom,
          color: 'rgba(16, 185, 129, 0.8)',
          lineStyle: LineStyle.Dotted,
          lineWidth: 1,
          axisLabelVisible: true,
          title: 'Discount',
        }),
      );
    }

    // Structure markers (BOS + CHoCH)
    const markers: SeriesMarker<Time>[] = [];
    if (toggles.structure) {
      for (const b of (data.structure?.bos || [])) {
        markers.push({
          time: b.time as UTCTimestamp,
          position: b.type === 'bullish' ? 'belowBar' : 'aboveBar',
          color: b.type === 'bullish' ? '#10b981' : '#ef4444',
          shape: b.type === 'bullish' ? 'arrowUp' : 'arrowDown',
          text: 'BOS',
        });
      }
      for (const c of (data.structure?.choch || [])) {
        markers.push({
          time: c.time as UTCTimestamp,
          position: c.type === 'bullish' ? 'belowBar' : 'aboveBar',
          color: '#22d3ee',
          shape: 'circle',
          text: 'CHoCH',
        });
      }
      markers.sort((a, b) => (a.time as number) - (b.time as number));
    }
    candleSeries.setMarkers(markers);
  }, [data, toggles]);

  const handleRefresh = async () => {
    try {
      await refetch();
      toast.success('Données SMC rafraîchies');
    } catch (e: any) {
      toast.error(`Erreur: ${e?.message || 'inconnue'}`);
    }
  };

  const signal = data?.signal;
  const htfBias = data?.htf_bias;
  const pd = data?.premium_discount;
  const sessions = data?.sessions || [];

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Smart Graph SMC</h1>
          <p className="text-sm text-muted mt-1">
            Analyse SMC/ICT temps réel · Order Blocks · Liquidity · FVG · Structure
          </p>
        </div>
        <Button onClick={handleRefresh} disabled={isFetching} variant="primary">
          {isFetching ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          Refresh
        </Button>
      </div>

      {/* Controls */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-4">
          <div>
            <label className="text-xs text-dim block mb-1.5">Symbole</label>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="w-40 px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              placeholder="BTC/USDC"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Timeframe</label>
            <div className="flex gap-1">
              {TIMEFRAMES.map((tf) => (
                <Button
                  key={tf}
                  size="sm"
                  variant={tf === timeframe ? 'primary' : 'default'}
                  onClick={() => setTimeframe(tf)}
                >
                  {tf}
                </Button>
              ))}
            </div>
          </div>
          <div className="flex-1" />
          {/* Overlay toggles */}
          <div className="flex flex-wrap gap-3">
            {([
              ['orderBlocks', 'Order Blocks', Layers],
              ['liquidityPools', 'Liquidity', Droplets],
              ['fvg', 'FVG', Waves],
              ['trendlines', 'Trendlines', GitBranch],
              ['structure', 'BOS/CHoCH', Activity],
              ['premiumDiscount', 'Premium/Discount', Target],
            ] as const).map(([key, label, Icon]) => (
              <label key={key} className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={toggles[key]}
                  onChange={(e) => setToggles({ ...toggles, [key]: e.target.checked })}
                  className="rounded"
                />
                <Icon className="w-3.5 h-3.5 text-muted" />
                {label}
              </label>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
        </div>
      )}

      {/* Error */}
      {isError && (
        <Card>
          <CardContent className="flex items-center gap-3 text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
            <AlertCircle className="w-4 h-4" />
            <span>Erreur: {(error as any)?.message || 'inconnue'}</span>
          </CardContent>
        </Card>
      )}

      {/* Chart + side panel */}
      {!isLoading && !isError && data && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          {/* Chart */}
          <Card className="lg:col-span-3 p-0 overflow-hidden">
            <div className="h-[500px] w-full" ref={containerRef} />
          </Card>

          {/* Side panel */}
          <div className="space-y-4">
            {/* Signal */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="w-3.5 h-3.5 text-primary-400" />
                  Signal
                </CardTitle>
              </CardHeader>
              <CardContent>
                {signal ? (
                  <div className="space-y-3 text-sm">
                    <div className="flex items-center gap-2">
                      {signal.side === 'long' && <ArrowUp className="w-4 h-4 text-emerald-400" />}
                      {signal.side === 'short' && <ArrowDown className="w-4 h-4 text-red-400" />}
                      <Badge variant={signal.side === 'long' ? 'success' : signal.side === 'short' ? 'danger' : 'default'}>
                        {String(signal.side || '').toUpperCase()}
                      </Badge>
                      <span className="text-xs text-muted">Score {Number(signal.score ?? 0).toFixed(2)}</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-xs">
                      <div>
                        <div className="text-dim flex items-center gap-1"><Target className="w-3 h-3" />Entry</div>
                        <div className="font-mono text-emerald-400">{formatUSD(Number(signal.entry ?? 0))}</div>
                      </div>
                      <div>
                        <div className="text-dim flex items-center gap-1"><Shield className="w-3 h-3" />Stop</div>
                        <div className="font-mono text-red-400">{formatUSD(Number(signal.stop ?? 0))}</div>
                      </div>
                      <div>
                        <div className="text-dim flex items-center gap-1"><ArrowUp className="w-3 h-3" />TP</div>
                        <div className="font-mono text-emerald-400">{formatUSD(Number(signal.tp ?? 0))}</div>
                      </div>
                    </div>
                    {signal.reason && (
                      <div className="text-xs text-muted bg-card-hover p-2 rounded border border-border">
                        {signal.reason}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-muted">Pas de signal</div>
                )}
              </CardContent>
            </Card>

            {/* HTF Bias */}
            {htfBias && (
              <Card>
                <CardHeader><CardTitle>HTF Bias ({htfBias.tf})</CardTitle></CardHeader>
                <CardContent>
                  <Badge variant={htfBias.bias === 'bullish' ? 'success' : htfBias.bias === 'bearish' ? 'danger' : 'default'}>
                    {String(htfBias.bias || '').toUpperCase()}
                  </Badge>
                </CardContent>
              </Card>
            )}

            {/* Premium / Discount */}
            {pd && (
              <Card>
                <CardHeader><CardTitle>Premium / Discount</CardTitle></CardHeader>
                <CardContent className="space-y-1 text-xs font-mono">
                  <div className="flex justify-between">
                    <span className="text-red-400">Premium</span>
                    <span>{formatUSD(Number(pd.premium_top ?? 0))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Equilibrium</span>
                    <span>{formatUSD(Number(pd.equilibrium ?? 0))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-emerald-400">Discount</span>
                    <span>{formatUSD(Number(pd.discount_bottom ?? 0))}</span>
                  </div>
                  <div className="pt-2 border-t border-border">
                    <Badge variant={pd.current_zone === 'premium' ? 'danger' : pd.current_zone === 'discount' ? 'success' : 'default'}>
                      Zone: {String(pd.current_zone || '').toUpperCase()}
                    </Badge>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Sessions */}
            {sessions.length > 0 && (
              <Card>
                <CardHeader><CardTitle>Sessions</CardTitle></CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-2">
                    {sessions.map((s: any, i: number) => (
                      <Badge key={i} variant="info">
                        {s.name} · {formatTime(s.start_time)}–{formatTime(s.end_time)}
                      </Badge>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}

      {/* Bottom tables: OBs and LPs */}
      {!isLoading && !isError && data && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Order Blocks récents</CardTitle>
              <Badge variant="info">{data.order_blocks?.length ?? 0}</Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto max-h-80">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card">
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2 font-medium">Type</th>
                      <th className="p-2 font-medium text-right">Top</th>
                      <th className="p-2 font-medium text-right">Bottom</th>
                      <th className="p-2 font-medium">Status</th>
                      <th className="p-2 font-medium text-right">Strength</th>
                      <th className="p-2 font-medium">Start</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.order_blocks || []).slice(0, 30).map((ob: any, i: number) => (
                      <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-2">
                          <span className={cn('font-semibold', ob.kind === 'bullish' ? 'text-emerald-400' : 'text-red-400')}>
                            {ob.kind === 'bullish' ? 'BULL' : 'BEAR'}
                          </span>
                        </td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(ob.top ?? 0))}</td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(ob.bottom ?? 0))}</td>
                        <td className="p-2">
                          <Badge variant={ob.status === 'fresh' ? 'success' : ob.status === 'touched' ? 'warning' : 'danger'}>
                            {ob.status}
                          </Badge>
                        </td>
                        <td className="p-2 text-right font-mono text-muted">
                          {ob.strength != null ? Number(ob.strength).toFixed(2) : '—'}
                        </td>
                        <td className="p-2 text-xs text-muted font-mono">{formatDateTime(ob.time_start)}</td>
                      </tr>
                    ))}
                    {(data.order_blocks || []).length === 0 && (
                      <tr>
                        <td colSpan={6} className="p-4 text-center text-muted">Aucun Order Block</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Liquidity Pools</CardTitle>
              <Badge variant="info">{data.liquidity_pools?.length ?? 0}</Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto max-h-80">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card">
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2 font-medium">Type</th>
                      <th className="p-2 font-medium text-right">Level</th>
                      <th className="p-2 font-medium">Status</th>
                      <th className="p-2 font-medium text-right">Touches</th>
                      <th className="p-2 font-medium">Start</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.liquidity_pools || []).slice(0, 30).map((lp: any, i: number) => (
                      <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-2">
                          <span className={cn('font-semibold', lp.kind === 'buyside' ? 'text-emerald-400' : 'text-red-400')}>
                            {lp.kind === 'buyside' ? 'BUY' : 'SELL'}
                          </span>
                        </td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(lp.level ?? 0))}</td>
                        <td className="p-2">
                          <Badge variant={lp.status === 'active' ? 'success' : 'warning'}>
                            {lp.status}
                          </Badge>
                        </td>
                        <td className="p-2 text-right font-mono text-muted">{lp.n_touches ?? 0}</td>
                        <td className="p-2 text-xs text-muted font-mono">{formatDateTime(lp.time_start)}</td>
                      </tr>
                    ))}
                    {(data.liquidity_pools || []).length === 0 && (
                      <tr>
                        <td colSpan={5} className="p-4 text-center text-muted">Aucun Liquidity Pool</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
