'use client';

import { useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatTime, formatDateTime, formatPct } from '@/lib/utils';
import { useSMC } from '@/hooks/use-api';
import { TradePlansTable } from '@/components/cards/trade-plans-table';
import { toast } from 'sonner';
import {
  Loader2, RefreshCw, AlertCircle, Activity,
  ArrowUp, ArrowDown, Target, Shield, Layers, Droplets, Waves, GitBranch, Sparkles,
  Box, Ban, BarChart3, TrendingUp, Recycle, Spline, Flame, CircleDot,
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
  liquidityVoids: boolean;
  breakers: boolean;
  rejectionBlocks: boolean;
  trendlines: boolean;
  channel: boolean;
  structure: boolean;
  swingLabels: boolean;
  structureLine: boolean;
  premiumDiscount: boolean;
  volumeProfile: boolean;
  cycle: boolean;
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
  time: number[], open: number[], high: number[], low: number[], close: number[],
): CandleRow[] {
  const seen = new Set<number>();
  const out: CandleRow[] = [];
  for (let i = 0; i < time.length; i++) {
    const t = time[i];
    if (!Number.isFinite(t)) continue;
    if (seen.has(t)) continue;
    if (out.length > 0 && t < (out[out.length - 1].time as number)) continue;
    seen.add(t);
    out.push({ time: t as UTCTimestamp, open: open[i], high: high[i], low: low[i], close: close[i] });
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
    liquidityVoids: false,
    breakers: false,
    rejectionBlocks: false,
    trendlines: true,
    channel: false,
    structure: true,
    swingLabels: false,
    structureLine: false,
    premiumDiscount: true,
    volumeProfile: false,
    cycle: false,
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
      upColor: '#10b981', downColor: '#ef4444',
      borderUpColor: '#10b981', borderDownColor: '#ef4444',
      wickUpColor: '#10b981', wickDownColor: '#ef4444',
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

  // Helper : dessine un rectangle (zone SMC) avec 2 line series (top + bottom)
  function drawRect(
    chart: IChartApi,
    ts: UTCTimestamp,
    te: UTCTimestamp,
    top: number,
    bottom: number,
    color: string,
    lineStyle: LineStyle = LineStyle.Solid,
    lineWidth: 1 | 2 = 1,
  ) {
    if (te < ts) return;
    const topSeries = chart.addLineSeries({
      color, lineWidth, lineStyle,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    topSeries.setData([{ time: ts, value: top }, { time: te, value: top }]);
    const botSeries = chart.addLineSeries({
      color, lineWidth, lineStyle,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    botSeries.setData([{ time: ts, value: bottom }, { time: te, value: bottom }]);
    overlaysRef.current.push(topSeries, botSeries);
  }

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
    const firstTime = ohlcvTime[0] as UTCTimestamp;

    // ── Order Blocks (rectangles pleins) ─────────────────────────────────
    if (toggles.orderBlocks && Array.isArray(data.order_blocks)) {
      for (const ob of data.order_blocks) {
        const bullish = ob.kind === 'bullish';
        const color = bullish ? 'rgba(16, 185, 129, 0.7)' : 'rgba(239, 68, 68, 0.7)';
        drawRect(
          chart,
          (ob.time_start ?? firstTime) as UTCTimestamp,
          (ob.time_end ?? lastTime) as UTCTimestamp,
          Number(ob.top), Number(ob.bottom),
          color, LineStyle.Solid, 2,
        );
      }
    }

    // ── FVG (rectangles pointillés) ──────────────────────────────────────
    if (toggles.fvg && Array.isArray(data.fvgs)) {
      for (const f of data.fvgs) {
        const bullish = f.kind === 'bullish';
        const color = bullish ? 'rgba(34, 211, 238, 0.6)' : 'rgba(245, 158, 11, 0.6)';
        drawRect(
          chart,
          (f.time_start ?? firstTime) as UTCTimestamp,
          (f.time_end ?? lastTime) as UTCTimestamp,
          Number(f.top), Number(f.bottom),
          color, LineStyle.Dotted, 1,
        );
      }
    }

    // ── Liquidity Voids (rectangles larges semi-transparents) ────────────
    if (toggles.liquidityVoids && Array.isArray(data.liquidity_voids)) {
      for (const vd of data.liquidity_voids) {
        const bullish = vd.kind === 'bullish';
        const color = bullish ? 'rgba(139, 92, 246, 0.4)' : 'rgba(168, 85, 247, 0.4)';
        drawRect(
          chart,
          (vd.time_start ?? firstTime) as UTCTimestamp,
          (vd.time_end ?? lastTime) as UTCTimestamp,
          Number(vd.top), Number(vd.bottom),
          color, LineStyle.LargeDashed, 1,
        );
      }
    }

    // ── Breaker Blocks (rectangles avec bordure épaisse) ─────────────────
    if (toggles.breakers && Array.isArray(data.breakers)) {
      for (const brk of data.breakers) {
        const bullish = brk.kind === 'bullish';
        const color = bullish ? 'rgba(34, 211, 238, 0.8)' : 'rgba(245, 158, 11, 0.8)';
        drawRect(
          chart,
          (brk.time_start ?? firstTime) as UTCTimestamp,
          (brk.time_end ?? lastTime) as UTCTimestamp,
          Number(brk.top), Number(brk.bottom),
          color, LineStyle.Solid, 2,
        );
      }
    }

    // ── Rejection Blocks (rectangles fins) ───────────────────────────────
    if (toggles.rejectionBlocks && Array.isArray(data.rejection_blocks)) {
      for (const rb of data.rejection_blocks) {
        const bullish = rb.kind === 'bullish';
        const color = bullish ? 'rgba(16, 185, 129, 0.5)' : 'rgba(239, 68, 68, 0.5)';
        drawRect(
          chart,
          (rb.time_start ?? firstTime) as UTCTimestamp,
          (rb.time_end ?? lastTime) as UTCTimestamp,
          Number(rb.top), Number(rb.bottom),
          color, LineStyle.Dashed, 1,
        );
      }
    }

    // ── Trendlines (diagonales dashed) ───────────────────────────────────
    if (toggles.trendlines && Array.isArray(data.trendlines)) {
      for (const tl of data.trendlines) {
        const color = tl.kind === 'support' ? 'rgba(16, 185, 129, 0.9)' : 'rgba(239, 68, 68, 0.9)';
        const lineSeries = chart.addLineSeries({
          color, lineWidth: 2, lineStyle: LineStyle.Dashed,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        lineSeries.setData([
          { time: (tl.time1 ?? firstTime) as UTCTimestamp, value: Number(tl.y1) },
          { time: (tl.time2 ?? lastTime) as UTCTimestamp, value: Number(tl.y2) },
        ]);
        overlaysRef.current.push(lineSeries);
      }
    }

    // ── Channel (canal de régression : mid + upper + lower) ──────────────
    if (toggles.channel && data.channel) {
      const ch = data.channel;
      const ts = (ch.time_start ?? firstTime) as UTCTimestamp;
      const te = (ch.time_end ?? lastTime) as UTCTimestamp;
      const hw = Number(ch.half_width || 0);
      const midSeries = chart.addLineSeries({
        color: 'rgba(139, 92, 246, 0.8)', lineWidth: 2, lineStyle: LineStyle.Solid,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      midSeries.setData([
        { time: ts, value: Number(ch.mid_start) },
        { time: te, value: Number(ch.mid_end) },
      ]);
      const upperSeries = chart.addLineSeries({
        color: 'rgba(139, 92, 246, 0.4)', lineWidth: 1, lineStyle: LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      upperSeries.setData([
        { time: ts, value: Number(ch.mid_start) + hw },
        { time: te, value: Number(ch.mid_end) + hw },
      ]);
      const lowerSeries = chart.addLineSeries({
        color: 'rgba(139, 92, 246, 0.4)', lineWidth: 1, lineStyle: LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      lowerSeries.setData([
        { time: ts, value: Number(ch.mid_start) - hw },
        { time: te, value: Number(ch.mid_end) - hw },
      ]);
      overlaysRef.current.push(midSeries, upperSeries, lowerSeries);
    }

    // ── Structure line (zigzag peaks/troughs) ────────────────────────────
    if (toggles.structureLine && Array.isArray(data.structure_line)) {
      const sl = data.structure_line;
      if (sl.length >= 2) {
        const zigSeries = chart.addLineSeries({
          color: 'rgba(156, 163, 175, 0.6)', lineWidth: 1, lineStyle: LineStyle.Solid,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        zigSeries.setData(
          sl.map((pt: any) => ({
            time: pt.time as UTCTimestamp,
            value: Number(pt.price),
          })).filter((p: any) => p.time != null),
        );
        overlaysRef.current.push(zigSeries);
      }
    }

    // ── Cycle projection (ligne de projection + target) ──────────────────
    if (toggles.cycle && data.cycle) {
      const cy = data.cycle;
      if (cy.from_time != null && cy.target != null) {
        const projSeries = chart.addLineSeries({
          color: 'rgba(245, 158, 11, 0.8)', lineWidth: 2, lineStyle: LineStyle.LargeDashed,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        projSeries.setData([
          { time: cy.from_time as UTCTimestamp, value: Number(cy.from_price) },
          { time: lastTime, value: Number(cy.target) },
        ]);
        overlaysRef.current.push(projSeries);
        priceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: Number(cy.target),
            color: 'rgba(245, 158, 11, 0.6)',
            lineStyle: LineStyle.Dashed, lineWidth: 1,
            axisLabelVisible: true, title: `Cycle target`,
          }),
        );
        if (cy.boundary != null) {
          priceLinesRef.current.push(
            candleSeries.createPriceLine({
              price: Number(cy.boundary),
              color: 'rgba(245, 158, 11, 0.4)',
              lineStyle: LineStyle.Dotted, lineWidth: 1,
              axisLabelVisible: true, title: `Cycle boundary`,
            }),
          );
        }
      }
    }

    // ── Liquidity Pools (lignes horizontales) ────────────────────────────
    if (toggles.liquidityPools && Array.isArray(data.liquidity_pools)) {
      for (const lp of data.liquidity_pools) {
        const buyside = lp.kind === 'buyside';
        const color = buyside ? 'rgba(16, 185, 129, 0.85)' : 'rgba(239, 68, 68, 0.85)';
        const pl = candleSeries.createPriceLine({
          price: Number(lp.level),
          color,
          lineStyle: LineStyle.Dashed, lineWidth: 1,
          axisLabelVisible: true,
          title: `LP ${lp.kind}${lp.status === 'swept' ? ' ✕' : ''}`,
        });
        priceLinesRef.current.push(pl);
      }
    }

    // ── Volume Profile (POC + HVN + LVN) ─────────────────────────────────
    if (toggles.volumeProfile && data.volume_profile) {
      const vp = data.volume_profile;
      if (vp.poc != null) {
        priceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: Number(vp.poc),
            color: 'rgba(34, 211, 238, 0.9)',
            lineStyle: LineStyle.Solid, lineWidth: 2,
            axisLabelVisible: true, title: 'POC',
          }),
        );
      }
      for (const hvn of (vp.hvns || [])) {
        priceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: Number(hvn),
            color: 'rgba(34, 211, 238, 0.4)',
            lineStyle: LineStyle.Dashed, lineWidth: 1,
            axisLabelVisible: false, title: 'HVN',
          }),
        );
      }
      for (const lvn of (vp.lvns || [])) {
        priceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: Number(lvn),
            color: 'rgba(245, 158, 11, 0.4)',
            lineStyle: LineStyle.Dotted, lineWidth: 1,
            axisLabelVisible: false, title: 'LVN',
          }),
        );
      }
    }

    // ── Premium / Discount (3 lignes horizontales) ───────────────────────
    if (toggles.premiumDiscount && data.premium_discount) {
      const pd = data.premium_discount;
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: Number(pd.premium_top),
          color: 'rgba(239, 68, 68, 0.8)',
          lineStyle: LineStyle.Dotted, lineWidth: 1,
          axisLabelVisible: true, title: 'Premium',
        }),
        candleSeries.createPriceLine({
          price: Number(pd.equilibrium),
          color: 'rgba(156, 163, 175, 0.8)',
          lineStyle: LineStyle.Dashed, lineWidth: 1,
          axisLabelVisible: true, title: 'Equilibrium',
        }),
        candleSeries.createPriceLine({
          price: Number(pd.discount_bottom),
          color: 'rgba(16, 185, 129, 0.8)',
          lineStyle: LineStyle.Dotted, lineWidth: 1,
          axisLabelVisible: true, title: 'Discount',
        }),
      );
    }

    // ── Markers (BOS + CHoCH + SWEEP + SWING labels) ─────────────────────
    const markers: SeriesMarker<Time>[] = [];
    if (toggles.structure && Array.isArray(data.markers)) {
      for (const m of data.markers) {
        if (m.type === 'BOS' || m.type === 'bos') {
          markers.push({
            time: m.time as UTCTimestamp,
            position: m.direction === 'up' ? 'belowBar' : 'aboveBar',
            color: m.direction === 'up' ? '#10b981' : '#ef4444',
            shape: m.direction === 'up' ? 'arrowUp' : 'arrowDown',
            text: 'BOS',
          });
        } else if (m.type === 'CHoCH' || m.type === 'choch') {
          markers.push({
            time: m.time as UTCTimestamp,
            position: m.direction === 'up' ? 'belowBar' : 'aboveBar',
            color: '#22d3ee',
            shape: 'circle',
            text: 'CHoCH',
          });
        } else if (m.type === 'SWEEP') {
          markers.push({
            time: m.time as UTCTimestamp,
            position: m.direction === 'up' ? 'belowBar' : 'aboveBar',
            color: m.rejected ? '#f59e0b' : '#a78bfa',
            shape: m.direction === 'up' ? 'arrowUp' : 'arrowDown',
            text: `SWEEP${m.rejected ? '!' : ''}`,
          });
        }
      }
    }
    if (toggles.swingLabels && Array.isArray(data.swing_labels)) {
      for (const sw of data.swing_labels) {
        markers.push({
          time: sw.time as UTCTimestamp,
          position: sw.kind === 'H' ? 'aboveBar' : 'belowBar',
          color: sw.kind === 'H' ? '#ef4444' : '#10b981',
          shape: sw.kind === 'H' ? 'circle' : 'square',
          text: sw.label,
        });
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
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
  const session = data?.session;
  const vp = data?.volume_profile;
  const cycle = data?.cycle;
  const bias = data?.bias;
  const channel = data?.channel;

  const toggleList: Array<[keyof OverlayToggles, string, any]> = [
    ['orderBlocks', 'Order Blocks', Layers],
    ['liquidityPools', 'Liquidity', Droplets],
    ['fvg', 'FVG', Waves],
    ['liquidityVoids', 'Liq. Voids', Box],
    ['breakers', 'Breakers', Flame],
    ['rejectionBlocks', 'Rejections', Ban],
    ['trendlines', 'Trendlines', GitBranch],
    ['channel', 'Channel', Spline],
    ['structure', 'BOS/CHoCH', Activity],
    ['swingLabels', 'Swings', CircleDot],
    ['structureLine', 'Zigzag', TrendingUp],
    ['premiumDiscount', 'Prem/Disc', Target],
    ['volumeProfile', 'Vol Profile', BarChart3],
    ['cycle', 'Cycle', Recycle],
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Smart Graph SMC</h1>
          <p className="text-sm text-muted mt-1">
            Analyse SMC/ICT complète · Order Blocks · Liquidity · FVG · Voids · Breakers · Rejections · Volume Profile · Cycle · Channel · Structure
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
            {toggleList.map(([key, label, Icon]) => (
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
                    {signal.setup && (
                      <div className="text-xs text-cyan-400 font-mono">Setup: {signal.setup}</div>
                    )}
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
                    {signal.gain_pct != null && (
                      <div className="text-xs text-muted">Gain potentiel: {Number(signal.gain_pct).toFixed(2)}% · RR: {Number(signal.rr ?? 0).toFixed(2)}</div>
                    )}
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

            {/* Bias global + HTF Bias */}
            <Card>
              <CardHeader><CardTitle>Bias</CardTitle></CardHeader>
              <CardContent className="space-y-2">
                {bias != null && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted">Current TF</span>
                    <Badge variant={bias === 'bullish' ? 'success' : bias === 'bearish' ? 'danger' : 'default'}>
                      {String(bias || 'neutral').toUpperCase()}
                    </Badge>
                  </div>
                )}
                {htfBias && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted">HTF (×{htfBias.n_htf ?? '?'})</span>
                    <Badge variant={htfBias.trend === 1 ? 'success' : htfBias.trend === -1 ? 'danger' : 'default'}>
                      {htfBias.label || (htfBias.trend === 1 ? 'haussier' : htfBias.trend === -1 ? 'baissier' : 'neutre')}
                    </Badge>
                  </div>
                )}
              </CardContent>
            </Card>

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

            {/* Cycle projection */}
            {cycle && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Recycle className="w-3.5 h-3.5 text-amber-400" />
                    Cycle
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-1 text-xs font-mono">
                  <div className="flex justify-between">
                    <span className="text-muted">Phase</span>
                    <span className="text-amber-400 capitalize">{cycle.phase}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Progress</span>
                    <span>{(Number(cycle.progress ?? 0) * 100).toFixed(0)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Boundary</span>
                    <span>{formatUSD(Number(cycle.boundary ?? 0))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-amber-400">Target</span>
                    <span className="text-amber-400">{formatUSD(Number(cycle.target ?? 0))}</span>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Volume Profile */}
            {vp && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <BarChart3 className="w-3.5 h-3.5 text-cyan-400" />
                    Volume Profile
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                  <div className="flex justify-between font-mono">
                    <span className="text-cyan-400">POC</span>
                    <span>{formatUSD(Number(vp.poc ?? 0))}</span>
                  </div>
                  {vp.hvns?.length > 0 && (
                    <div>
                      <div className="text-dim mb-1">HVN ({vp.hvns.length})</div>
                      <div className="flex flex-wrap gap-1">
                        {vp.hvns.slice(0, 4).map((h: number, i: number) => (
                          <span key={i} className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400">
                            {formatUSD(Number(h), { decimals: 0 })}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {vp.lvns?.length > 0 && (
                    <div>
                      <div className="text-dim mb-1">LVN ({vp.lvns.length})</div>
                      <div className="flex flex-wrap gap-1">
                        {vp.lvns.slice(0, 4).map((l: number, i: number) => (
                          <span key={i} className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400">
                            {formatUSD(Number(l), { decimals: 0 })}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Channel */}
            {channel && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Spline className="w-3.5 h-3.5 text-purple-400" />
                    Channel
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-1 text-xs font-mono">
                  <div className="flex justify-between">
                    <span className="text-muted">Mid start</span>
                    <span>{formatUSD(Number(channel.mid_start ?? 0))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Mid end</span>
                    <span>{formatUSD(Number(channel.mid_end ?? 0))}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Half width</span>
                    <span>{formatUSD(Number(channel.half_width ?? 0))}</span>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Session (killzone) */}
            {session && (
              <Card>
                <CardHeader><CardTitle>Session</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-muted">Active</span>
                    <Badge variant="info" className="capitalize">{session.name}</Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted">Killzone</span>
                    <Badge variant={session.in_killzone ? 'warning' : 'default'}>
                      {session.in_killzone ? '⚠ In killzone' : 'No'}
                    </Badge>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}

      {/*
        Plans recommandés — la table inline précédente n'exposait que 7 des 10
        colonnes de l'ancienne UI (ni Statut, ni Gain, ni Dist, ni Score) et
        tronquait à 20 lignes sans tri, ce qui masquait potentiellement les
        meilleurs plans.
      */}
      {!isLoading && !isError && data?.trade_plans?.length > 0 && (
        <TradePlansTable plans={data.trade_plans} />
      )}

      {/* Bottom tables: all SMC entities */}
      {!isLoading && !isError && data && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Order Blocks */}
          <Card>
            <CardHeader>
              <CardTitle>Order Blocks</CardTitle>
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
                      <tr><td colSpan={6} className="p-4 text-center text-muted">Aucun Order Block</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* Liquidity Pools */}
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
                      <tr><td colSpan={5} className="p-4 text-center text-muted">Aucun Liquidity Pool</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* FVGs */}
          <Card>
            <CardHeader>
              <CardTitle>FVG (Fair Value Gaps)</CardTitle>
              <Badge variant="info">{data.fvgs?.length ?? 0}</Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto max-h-60">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card">
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2 font-medium">Type</th>
                      <th className="p-2 font-medium text-right">Top</th>
                      <th className="p-2 font-medium text-right">Bottom</th>
                      <th className="p-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.fvgs || []).slice(0, 20).map((f: any, i: number) => (
                      <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-2">
                          <span className={cn('font-semibold', f.kind === 'bullish' ? 'text-cyan-400' : 'text-amber-400')}>
                            {f.kind === 'bullish' ? 'BULL' : 'BEAR'}
                          </span>
                        </td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(f.top ?? 0))}</td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(f.bottom ?? 0))}</td>
                        <td className="p-2">
                          <Badge variant={f.status === 'open' ? 'success' : f.status === 'mitigated' ? 'warning' : 'danger'}>
                            {f.status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                    {(data.fvgs || []).length === 0 && (
                      <tr><td colSpan={4} className="p-4 text-center text-muted">Aucun FVG</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* Liquidity Voids */}
          <Card>
            <CardHeader>
              <CardTitle>Liquidity Voids</CardTitle>
              <Badge variant="info">{data.liquidity_voids?.length ?? 0}</Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto max-h-60">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card">
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2 font-medium">Type</th>
                      <th className="p-2 font-medium text-right">Top</th>
                      <th className="p-2 font-medium text-right">Bottom</th>
                      <th className="p-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.liquidity_voids || []).slice(0, 20).map((vd: any, i: number) => (
                      <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-2">
                          <span className={cn('font-semibold', vd.kind === 'bullish' ? 'text-purple-400' : 'text-purple-400')}>
                            {vd.kind === 'bullish' ? 'BULL' : 'BEAR'}
                          </span>
                        </td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(vd.top ?? 0))}</td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(vd.bottom ?? 0))}</td>
                        <td className="p-2">
                          <Badge variant={vd.status === 'open' ? 'success' : vd.status === 'mitigated' ? 'warning' : 'danger'}>
                            {vd.status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                    {(data.liquidity_voids || []).length === 0 && (
                      <tr><td colSpan={4} className="p-4 text-center text-muted">Aucun Liquidity Void</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* Breakers */}
          <Card>
            <CardHeader>
              <CardTitle>Breaker Blocks</CardTitle>
              <Badge variant="info">{data.breakers?.length ?? 0}</Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto max-h-60">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card">
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2 font-medium">Type</th>
                      <th className="p-2 font-medium text-right">Top</th>
                      <th className="p-2 font-medium text-right">Bottom</th>
                      <th className="p-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.breakers || []).slice(0, 20).map((brk: any, i: number) => (
                      <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-2">
                          <span className={cn('font-semibold', brk.kind === 'bullish' ? 'text-cyan-400' : 'text-amber-400')}>
                            {brk.kind === 'bullish' ? 'BULL' : 'BEAR'}
                          </span>
                        </td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(brk.top ?? 0))}</td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(brk.bottom ?? 0))}</td>
                        <td className="p-2">
                          <Badge variant={brk.status === 'fresh' ? 'success' : brk.status === 'touched' ? 'warning' : 'danger'}>
                            {brk.status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                    {(data.breakers || []).length === 0 && (
                      <tr><td colSpan={4} className="p-4 text-center text-muted">Aucun Breaker</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* Rejection Blocks */}
          <Card>
            <CardHeader>
              <CardTitle>Rejection Blocks</CardTitle>
              <Badge variant="info">{data.rejection_blocks?.length ?? 0}</Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto max-h-60">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card">
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2 font-medium">Type</th>
                      <th className="p-2 font-medium text-right">Top</th>
                      <th className="p-2 font-medium text-right">Bottom</th>
                      <th className="p-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.rejection_blocks || []).slice(0, 20).map((rb: any, i: number) => (
                      <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-2">
                          <span className={cn('font-semibold', rb.kind === 'bullish' ? 'text-emerald-400' : 'text-red-400')}>
                            {rb.kind === 'bullish' ? 'BULL' : 'BEAR'}
                          </span>
                        </td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(rb.top ?? 0))}</td>
                        <td className="p-2 text-right font-mono">{formatUSD(Number(rb.bottom ?? 0))}</td>
                        <td className="p-2">
                          <Badge variant={rb.status === 'fresh' ? 'success' : rb.status === 'touched' ? 'warning' : 'danger'}>
                            {rb.status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                    {(data.rejection_blocks || []).length === 0 && (
                      <tr><td colSpan={4} className="p-4 text-center text-muted">Aucun Rejection Block</td></tr>
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
