'use client';

import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatPct, formatDateTime } from '@/lib/utils';
import { useSMCReplay } from '@/hooks/use-api';
import { toast } from 'sonner';
import {
  Loader2, AlertCircle, Activity,
  SkipBack, SkipForward, ChevronLeft, ChevronRight,
  Play, Pause, Layers, Droplets, Waves,
} from 'lucide-react';
import {
  createChart, ColorType, LineStyle,
  type IChartApi, type ISeriesApi, type UTCTimestamp, type Time, type SeriesMarker,
} from 'lightweight-charts';

const TIMEFRAMES = ['15m', '30m', '1h', '4h'] as const;
const SPEEDS = [
  { label: '1x', ms: 1000 },
  { label: '2x', ms: 500 },
  { label: '5x', ms: 200 },
  { label: '10x', ms: 100 },
] as const;

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
    out.push({ time: t as UTCTimestamp, open: open[i], high: high[i], low: low[i], close: close[i] });
  }
  return out;
}

/** Resolve any "index-like" field on a replay entity to a bar index. */
function entityBarIndex(entity: any, fallbackKeys: string[] = ['created_at', 'index', 'confirmed_at']): number | null {
  if (!entity || typeof entity !== 'object') return null;
  for (const k of fallbackKeys) {
    if (typeof entity[k] === 'number' && Number.isFinite(entity[k])) return entity[k];
  }
  return null;
}

/** Lifecycle termination index (invalidation/sweep/fill). */
function entityEndIndex(entity: any): number | null {
  if (!entity || typeof entity !== 'object') return null;
  for (const k of ['invalidated_at', 'swept_at', 'filled_at']) {
    if (typeof entity[k] === 'number' && Number.isFinite(entity[k])) return entity[k];
  }
  return null;
}

function entityAliveAt(entity: any, currentIndex: number): boolean {
  const start = entityBarIndex(entity);
  if (start == null) return true; // no lifecycle info → always show
  if (currentIndex < start) return false;
  const end = entityEndIndex(entity);
  if (end != null && currentIndex >= end) return false;
  return true;
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function SmartReplayPage() {
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState<string>('4h');
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speedIdx, setSpeedIdx] = useState<number>(1); // 2x default

  const { data, isLoading, isError, error } = useSMCReplay(symbol, timeframe, 800);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlaysRef = useRef<ISeriesApi<any>[]>([]);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);

  const nBars = data?.n_bars ?? 0;
  const ohlcv = data?.ohlcv;
  const cleanedCandles = useMemo<CandleRow[]>(() => {
    if (!ohlcv) return [];
    return cleanOhlcv(ohlcv.time || [], ohlcv.open || [], ohlcv.high || [], ohlcv.low || [], ohlcv.close || []);
  }, [ohlcv]);

  // Clamp currentIndex when nBars changes
  useEffect(() => {
    if (nBars > 0) {
      setCurrentIndex((idx) => Math.max(0, Math.min(idx, nBars - 1)));
    } else {
      setCurrentIndex(0);
    }
  }, [nBars]);

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
        chart.applyOptions({ width: entry.contentRect.width, height: entry.contentRect.height });
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

  // Play/pause timer
  useEffect(() => {
    if (!isPlaying) return;
    const ms = SPEEDS[speedIdx].ms;
    const id = setInterval(() => {
      setCurrentIndex((idx) => {
        const next = idx + 1;
        if (next >= nBars - 1) {
          setIsPlaying(false);
          return nBars - 1;
        }
        return next;
      });
    }, ms);
    return () => clearInterval(id);
  }, [isPlaying, speedIdx, nBars]);

  // Update candles + overlays whenever currentIndex or data changes
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;

    // Slice candles up to currentIndex
    const sliceEnd = Math.max(1, Math.min(currentIndex + 1, cleanedCandles.length));
    candleSeries.setData(cleanedCandles.slice(0, sliceEnd));

    // Clear overlays
    for (const s of overlaysRef.current) {
      try { chart.removeSeries(s); } catch { /* noop */ }
    }
    overlaysRef.current = [];
    for (const pl of priceLinesRef.current) {
      try { candleSeries.removePriceLine(pl); } catch { /* noop */ }
    }
    priceLinesRef.current = [];

    if (!data || cleanedCandles.length === 0) {
      candleSeries.setMarkers([]);
      return;
    }
    const lastVisibleTime = cleanedCandles[sliceEnd - 1].time;

    // Order Blocks
    if (Array.isArray(data.order_blocks)) {
      for (const ob of data.order_blocks) {
        if (!entityAliveAt(ob, currentIndex)) continue;
        const bullish = ob.kind === 'bullish';
        const color = bullish ? 'rgba(16, 185, 129, 0.7)' : 'rgba(239, 68, 68, 0.7)';
        const ts = ob.time_start as UTCTimestamp | undefined;
        const te = (ob.time_end ?? lastVisibleTime) as UTCTimestamp;
        if (!ts || te < ts) continue;
        const cappedTe = (te > lastVisibleTime ? lastVisibleTime : te) as UTCTimestamp;
        const topSeries = chart.addLineSeries({
          color, lineWidth: 2, lineStyle: LineStyle.Solid,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        topSeries.setData([{ time: ts, value: ob.top }, { time: cappedTe, value: ob.top }]);
        const botSeries = chart.addLineSeries({
          color, lineWidth: 2, lineStyle: LineStyle.Solid,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        botSeries.setData([{ time: ts, value: ob.bottom }, { time: cappedTe, value: ob.bottom }]);
        overlaysRef.current.push(topSeries, botSeries);
      }
    }

    // FVG
    if (Array.isArray(data.fvgs)) {
      for (const f of data.fvgs) {
        if (!entityAliveAt(f, currentIndex)) continue;
        const bullish = f.kind === 'bullish';
        const color = bullish ? 'rgba(34, 211, 238, 0.6)' : 'rgba(245, 158, 11, 0.6)';
        const ts = f.time_start as UTCTimestamp | undefined;
        const te = (f.time_end ?? lastVisibleTime) as UTCTimestamp;
        if (!ts || te < ts) continue;
        const cappedTe = (te > lastVisibleTime ? lastVisibleTime : te) as UTCTimestamp;
        const topSeries = chart.addLineSeries({
          color, lineWidth: 1, lineStyle: LineStyle.Dotted,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        topSeries.setData([{ time: ts, value: f.top }, { time: cappedTe, value: f.top }]);
        const botSeries = chart.addLineSeries({
          color, lineWidth: 1, lineStyle: LineStyle.Dotted,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        botSeries.setData([{ time: ts, value: f.bottom }, { time: cappedTe, value: f.bottom }]);
        overlaysRef.current.push(topSeries, botSeries);
      }
    }

    // Liquidity Pools — horizontal price line
    if (Array.isArray(data.liquidity_pools)) {
      for (const lp of data.liquidity_pools) {
        if (!entityAliveAt(lp, currentIndex)) continue;
        const buyside = lp.kind === 'buyside';
        const color = buyside ? 'rgba(16, 185, 129, 0.85)' : 'rgba(239, 68, 68, 0.85)';
        const pl = candleSeries.createPriceLine({
          price: lp.level,
          color,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: `LP ${lp.kind}`,
        });
        priceLinesRef.current.push(pl);
      }
    }

    // Structure markers — filter by index when available
    const markers: SeriesMarker<Time>[] = [];
    for (const b of (data.structure?.bos || [])) {
      const bIdx = entityBarIndex(b, ['index', 'created_at']);
      if (bIdx != null && currentIndex < bIdx) continue;
      markers.push({
        time: b.time as UTCTimestamp,
        position: b.type === 'bullish' ? 'belowBar' : 'aboveBar',
        color: b.type === 'bullish' ? '#10b981' : '#ef4444',
        shape: b.type === 'bullish' ? 'arrowUp' : 'arrowDown',
        text: 'BOS',
      });
    }
    for (const c of (data.structure?.choch || [])) {
      const cIdx = entityBarIndex(c, ['index', 'created_at']);
      if (cIdx != null && currentIndex < cIdx) continue;
      markers.push({
        time: c.time as UTCTimestamp,
        position: c.type === 'bullish' ? 'belowBar' : 'aboveBar',
        color: '#22d3ee',
        shape: 'circle',
        text: 'CHoCH',
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    candleSeries.setMarkers(markers);

    // Keep chart pinned to the latest visible bar
    chart.timeScale().scrollToRealTime();
  }, [data, currentIndex, cleanedCandles]);

  // ── Derived panel data ────────────────────────────────────────────────────

  const activeOrderBlocks = useMemo(() => {
    return (data?.order_blocks || []).filter((e: any) => entityAliveAt(e, currentIndex)).slice(-12).reverse();
  }, [data?.order_blocks, currentIndex]);

  const activeLiquidityPools = useMemo(() => {
    return (data?.liquidity_pools || []).filter((e: any) => entityAliveAt(e, currentIndex)).slice(-12).reverse();
  }, [data?.liquidity_pools, currentIndex]);

  const activeFvgs = useMemo(() => {
    return (data?.fvgs || []).filter((e: any) => entityAliveAt(e, currentIndex)).slice(-12).reverse();
  }, [data?.fvgs, currentIndex]);

  const lastStructure = useMemo(() => {
    const all: Array<{ kind: 'BOS' | 'CHoCH'; type: string; time: number; index: number | null }> = [];
    for (const b of (data?.structure?.bos || [])) {
      all.push({ kind: 'BOS', type: b.type, time: b.time, index: entityBarIndex(b, ['index', 'created_at']) });
    }
    for (const c of (data?.structure?.choch || [])) {
      all.push({ kind: 'CHoCH', type: c.type, time: c.time, index: entityBarIndex(c, ['index', 'created_at']) });
    }
    return all
      .filter((s) => s.index == null || s.index <= currentIndex)
      .sort((a, b) => (a.index ?? 0) - (b.index ?? 0))
      .pop() ?? null;
  }, [data?.structure, currentIndex]);

  const openTrades = useMemo(() => {
    return (data?.trades || []).filter((t: any) => {
      const entry = typeof t.entry_time === 'number' ? t.entry_time : null;
      const exit = typeof t.exit_time === 'number' ? t.exit_time : null;
      return entry != null && currentIndex >= entry && (exit == null || currentIndex < exit);
    });
  }, [data?.trades, currentIndex]);

  const closedTrades = useMemo(() => {
    return (data?.trades || [])
      .filter((t: any) => typeof t.exit_time === 'number' && currentIndex >= t.exit_time)
      .slice(-12)
      .reverse();
  }, [data?.trades, currentIndex]);

  // ── Controls ──────────────────────────────────────────────────────────────

  const goToStart = useCallback(() => { setIsPlaying(false); setCurrentIndex(0); }, []);
  const goToEnd = useCallback(() => { setIsPlaying(false); setCurrentIndex(Math.max(0, nBars - 1)); }, [nBars]);
  const stepBack = useCallback(() => { setIsPlaying(false); setCurrentIndex((i) => Math.max(0, i - 1)); }, []);
  const stepForward = useCallback(() => { setIsPlaying(false); setCurrentIndex((i) => Math.min(nBars - 1, i + 1)); }, [nBars]);
  const togglePlay = useCallback(() => {
    if (currentIndex >= nBars - 1) setCurrentIndex(0);
    setIsPlaying((p) => !p);
  }, [currentIndex, nBars]);

  const currentBarTime = cleanedCandles[currentIndex]?.time;
  const currentPrice = cleanedCandles[currentIndex]?.close;

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Smart Replay</h1>
          <p className="text-sm text-muted mt-1">
            Rejeu bougie par bougie · reconstruisez l'état SMC à n'importe quelle barre
          </p>
        </div>
        {nBars > 0 && (
          <Badge variant="info">
            Barre {currentIndex + 1} / {nBars} · {currentBarTime ? formatDateTime(currentBarTime as number) : '—'}
          </Badge>
        )}
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
          {currentPrice != null && (
            <div className="text-right">
              <div className="text-xs text-dim">Prix courant</div>
              <div className="font-mono text-sm font-semibold">{formatUSD(currentPrice)}</div>
            </div>
          )}
        </CardContent>
      </Card>

      {isError && (
        <Card>
          <CardContent className="flex items-center gap-3 text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
            <AlertCircle className="w-4 h-4" />
            <span>Erreur: {(error as any)?.message || 'inconnue'}</span>
          </CardContent>
        </Card>
      )}

      {isLoading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
        </div>
      )}

      {!isLoading && !isError && data && (
        <>
          {/* Chart + side panel */}
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
            <Card className="lg:col-span-3 p-0 overflow-hidden">
              <div className="h-[500px] w-full" ref={containerRef} />
            </Card>

            <div className="space-y-4">
              {/* Current structure */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Activity className="w-3.5 h-3.5 text-primary-400" />
                    Structure courante
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {lastStructure ? (
                    <div className="flex items-center gap-2">
                      <Badge variant={lastStructure.kind === 'BOS' ? 'info' : 'purple'}>
                        {lastStructure.kind}
                      </Badge>
                      <Badge variant={lastStructure.type === 'bullish' ? 'success' : 'danger'}>
                        {lastStructure.type?.toUpperCase()}
                      </Badge>
                      <span className="text-xs text-muted font-mono">
                        {formatDateTime(lastStructure.time)}
                      </span>
                    </div>
                  ) : (
                    <div className="text-xs text-muted">Aucune structure détectée</div>
                  )}
                </CardContent>
              </Card>

              {/* Open trades */}
              <Card>
                <CardHeader>
                  <CardTitle>Trades ouverts</CardTitle>
                  <Badge variant={openTrades.length > 0 ? 'success' : 'default'}>{openTrades.length}</Badge>
                </CardHeader>
                <CardContent className="space-y-2">
                  {openTrades.length === 0 ? (
                    <div className="text-xs text-muted">Aucun trade ouvert</div>
                  ) : (
                    openTrades.map((t: any, i: number) => (
                      <div key={i} className="text-xs space-y-0.5 border border-border rounded p-2 bg-card-hover">
                        <div className="flex items-center gap-2">
                          <Badge variant={t.side === 'long' ? 'success' : 'danger'}>
                            {t.side?.toUpperCase()}
                          </Badge>
                          <span className="font-mono text-muted">entry {formatUSD(Number(t.entry_price ?? 0))}</span>
                        </div>
                        {t.reason && <div className="text-dim">{t.reason}</div>}
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>

              {/* Active OBs quick list */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Layers className="w-3.5 h-3.5 text-emerald-400" />
                    OBs actifs
                  </CardTitle>
                  <Badge variant="info">{activeOrderBlocks.length}</Badge>
                </CardHeader>
                <CardContent className="space-y-1">
                  {activeOrderBlocks.length === 0 ? (
                    <div className="text-xs text-muted">Aucun OB actif</div>
                  ) : (
                    activeOrderBlocks.slice(0, 6).map((ob: any, i: number) => (
                      <div key={i} className="flex items-center justify-between text-xs">
                        <span className={ob.kind === 'bullish' ? 'text-emerald-400' : 'text-red-400'}>
                          {ob.kind === 'bullish' ? 'BULL' : 'BEAR'}
                        </span>
                        <span className="font-mono text-muted">
                          {formatUSD(Number(ob.bottom ?? 0))}–{formatUSD(Number(ob.top ?? 0))}
                        </span>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>

              {/* Active LPs quick list */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Droplets className="w-3.5 h-3.5 text-cyan-400" />
                    LPs actifs
                  </CardTitle>
                  <Badge variant="info">{activeLiquidityPools.length}</Badge>
                </CardHeader>
                <CardContent className="space-y-1">
                  {activeLiquidityPools.length === 0 ? (
                    <div className="text-xs text-muted">Aucun LP actif</div>
                  ) : (
                    activeLiquidityPools.slice(0, 6).map((lp: any, i: number) => (
                      <div key={i} className="flex items-center justify-between text-xs">
                        <span className={lp.kind === 'buyside' ? 'text-emerald-400' : 'text-red-400'}>
                          {lp.kind === 'buyside' ? 'BUY' : 'SELL'}
                        </span>
                        <span className="font-mono text-muted">{formatUSD(Number(lp.level ?? 0))}</span>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>

              {/* Active FVGs quick list */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Waves className="w-3.5 h-3.5 text-amber-400" />
                    FVGs ouverts
                  </CardTitle>
                  <Badge variant="info">{activeFvgs.length}</Badge>
                </CardHeader>
                <CardContent className="space-y-1">
                  {activeFvgs.length === 0 ? (
                    <div className="text-xs text-muted">Aucun FVG ouvert</div>
                  ) : (
                    activeFvgs.slice(0, 6).map((f: any, i: number) => (
                      <div key={i} className="flex items-center justify-between text-xs">
                        <span className={f.kind === 'bullish' ? 'text-emerald-400' : 'text-red-400'}>
                          {f.kind === 'bullish' ? 'BULL' : 'BEAR'}
                        </span>
                        <span className="font-mono text-muted">
                          {formatUSD(Number(f.bottom ?? 0))}–{formatUSD(Number(f.top ?? 0))}
                        </span>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>
            </div>
          </div>

          {/* Transport controls */}
          <Card>
            <CardContent className="flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <Button size="icon" variant="ghost" onClick={goToStart} title="Début">
                  <SkipBack className="w-4 h-4" />
                </Button>
                <Button size="icon" variant="ghost" onClick={stepBack} title="Reculer 1 barre">
                  <ChevronLeft className="w-4 h-4" />
                </Button>
                <Button size="icon" variant="primary" onClick={togglePlay} title="Play/Pause">
                  {isPlaying ? <Pause className="w-4 h-4" fill="currentColor" /> : <Play className="w-4 h-4" fill="currentColor" />}
                </Button>
                <Button size="icon" variant="ghost" onClick={stepForward} title="Avancer 1 barre">
                  <ChevronRight className="w-4 h-4" />
                </Button>
                <Button size="icon" variant="ghost" onClick={goToEnd} title="Fin">
                  <SkipForward className="w-4 h-4" />
                </Button>
              </div>

              <div className="flex items-center gap-2">
                <label className="text-xs text-dim">Vitesse</label>
                <select
                  value={speedIdx}
                  onChange={(e) => setSpeedIdx(Number(e.target.value))}
                  className="px-2 py-1 bg-card-hover border border-border rounded-md text-xs font-mono"
                >
                  {SPEEDS.map((s, i) => <option key={s.label} value={i}>{s.label}</option>)}
                </select>
              </div>

              <div className="flex-1 min-w-[200px]">
                <input
                  type="range"
                  min={0}
                  max={Math.max(0, nBars - 1)}
                  value={currentIndex}
                  onChange={(e) => { setIsPlaying(false); setCurrentIndex(Number(e.target.value)); }}
                  className="w-full accent-primary-400"
                />
              </div>

              <div className="text-xs font-mono text-muted">
                {currentIndex + 1} / {nBars}
              </div>
            </CardContent>
          </Card>

          {/* Closed trades */}
          <Card>
            <CardHeader>
              <CardTitle>Trades fermés</CardTitle>
              <Badge variant="default">{closedTrades.length}</Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto max-h-80">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card">
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2 font-medium">Side</th>
                      <th className="p-2 font-medium text-right">Entry</th>
                      <th className="p-2 font-medium text-right">Exit</th>
                      <th className="p-2 font-medium text-right">PnL</th>
                      <th className="p-2 font-medium text-right">PnL %</th>
                      <th className="p-2 font-medium">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {closedTrades.length === 0 ? (
                      <tr><td colSpan={6} className="p-4 text-center text-muted">Aucun trade fermé</td></tr>
                    ) : (
                      closedTrades.map((t: any, i: number) => (
                        <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                          <td className="p-2">
                            <span className={cn('font-semibold', t.side === 'long' ? 'text-emerald-400' : 'text-red-400')}>
                              {t.side?.toUpperCase()}
                            </span>
                          </td>
                          <td className="p-2 text-right font-mono">{formatUSD(Number(t.entry_price ?? 0))}</td>
                          <td className="p-2 text-right font-mono">{formatUSD(Number(t.exit_price ?? 0))}</td>
                          <td className={cn('p-2 text-right font-mono', (t.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                            {(t.pnl ?? 0) >= 0 ? '+' : ''}{formatUSD(Number(t.pnl ?? 0))}
                          </td>
                          <td className={cn('p-2 text-right font-mono', (t.pnl_pct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                            {formatPct(Number(t.pnl_pct ?? 0), 2, true)}
                          </td>
                          <td className="p-2 text-xs text-muted">{t.reason || '—'}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
