'use client';

/**
 * Onglet Smart Replay de `/market` — rejeu bougie par bougie des calques SMC.
 *
 * Lot Marché : cette vue était la page `/smartreplay`, vers laquelle l'onglet
 * se contentait de renvoyer par une `RedirectCard`. `/smartreplay` est
 * désormais en 308 vers `/market?tab=smartreplay`.
 */

import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatPct, formatDateTime } from '@/lib/utils';
import { useSMC, useSMCReplay } from '@/hooks/use-api';
import {
  TradePlansTable, RealizedTradesTable, type TradePlan,
} from '@/components/cards/trade-plans-table';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
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
import {
  ZonesPrimitive,
  ensureZonesPrimitive,
  buildZonesFromSmc,
} from '@/lib/smc-zones';

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

// ── Vue ─────────────────────────────────────────────────────────────────────

export function SmartReplayView() {
  const { defaultTf } = useTradingTimeframes('4h');
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState<string>(defaultTf || '4h');
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speedIdx, setSpeedIdx] = useState<number>(1); // 2x default
  const [selectedPlan, setSelectedPlan] = useState<TradePlan | null>(null);

  const { data, isLoading, isError, error } = useSMCReplay(symbol, timeframe, 800);
  // Plans SMC (analytiques) — même source que Smart Graph
  const { data: smcData } = useSMC(symbol, timeframe, 600);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlaysRef = useRef<ISeriesApi<any>[]>([]);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);
  const zonesPrimRef = useRef<ZonesPrimitive | null>(null);

  const nBars = data?.n_bars ?? 0;
  // API peut renvoyer `ohlcv` (colonnes) ou `candles` (liste d'objets).
  const cleanedCandles = useMemo<CandleRow[]>(() => {
    const ohlcv = data?.ohlcv;
    if (ohlcv?.time?.length) {
      return cleanOhlcv(
        ohlcv.time || [],
        ohlcv.open || [],
        ohlcv.high || [],
        ohlcv.low || [],
        ohlcv.close || [],
      );
    }
    const candles = data?.candles;
    if (Array.isArray(candles) && candles.length > 0) {
      const time: number[] = [];
      const open: number[] = [];
      const high: number[] = [];
      const low: number[] = [];
      const close: number[] = [];
      for (const c of candles) {
        if (c == null || typeof c !== 'object') continue;
        time.push(Number((c as any).time));
        open.push(Number((c as any).open));
        high.push(Number((c as any).high));
        low.push(Number((c as any).low));
        close.push(Number((c as any).close));
      }
      return cleanOhlcv(time, open, high, low, close);
    }
    return [];
  }, [data?.ohlcv, data?.candles]);

  // Clamp currentIndex when nBars changes
  useEffect(() => {
    if (nBars > 0) {
      setCurrentIndex((idx) => Math.max(0, Math.min(idx, nBars - 1)));
    } else {
      setCurrentIndex(0);
    }
  }, [nBars]);

  // Create chart once — autoSize + hauteur explicite (même fix que Smart Graph)
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#0f1419' },
        textColor: '#9ca3af',
        fontFamily: 'var(--font-jetbrains), monospace',
      },
      grid: {
        vertLines: { color: '#1f2937' },
        horzLines: { color: '#1f2937' },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#1f2937' },
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
    zonesPrimRef.current = ensureZonesPrimitive(candleSeries as any, null);

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          chart.applyOptions({ width, height });
        }
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      zonesPrimRef.current = null;
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
      zonesPrimRef.current?.setZones([]);
      return;
    }
    const lastVisibleTime = cleanedCandles[sliceEnd - 1].time;
    const timeAt = (idx: number | null | undefined): number | null => {
      if (idx == null || !Number.isFinite(idx)) return null;
      const i = Math.max(0, Math.min(Math.floor(idx), cleanedCandles.length - 1));
      return cleanedCandles[i]?.time as number ?? null;
    };

    // Zones remplies (OB / FVG / voids / breakers / rejections) — lifecycle causal
    if (zonesPrimRef.current) {
      const alive = (list: any[] | undefined) =>
        (list || []).filter((e: any) => entityAliveAt(e, currentIndex));
      const toTimed = (list: any[]) =>
        list.map((e: any) => {
          const startIdx = entityBarIndex(e);
          const endIdx = entityEndIndex(e);
          const t1 = timeAt(startIdx) ?? (e.time_start as number);
          let t2 = timeAt(endIdx) ?? (e.time_end as number) ?? (lastVisibleTime as number);
          if (t2 > (lastVisibleTime as number)) t2 = lastVisibleTime as number;
          return { ...e, time_start: t1, time_end: t2 };
        }).filter((e: any) => e.time_start != null && e.time_end != null);
      const zones = buildZonesFromSmc({
        order_blocks: toTimed(alive(data.order_blocks)),
        fvgs: toTimed(alive(data.fvgs)),
        liquidity_voids: toTimed(alive(data.voids || data.liquidity_voids)),
        breakers: toTimed(alive(data.breakers)),
        rejection_blocks: toTimed(alive(data.rejections || data.rejection_blocks)),
      }, {
        orderBlocks: true,
        fvg: true,
        liquidityVoids: true,
        breakers: true,
        rejectionBlocks: true,
        firstTime: cleanedCandles[0]?.time as number,
        lastTime: lastVisibleTime as number,
      });
      zonesPrimRef.current.setZones(zones);
    }

    // Liquidity Pools — horizontal price line
    if (Array.isArray(data.liquidity_pools || data.pools)) {
      for (const lp of (data.liquidity_pools || data.pools)) {
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

  // API replay : entry_bar / exit_bar (indices), entry / exit (prix)
  const openTrades = useMemo(() => {
    return (data?.trades || []).filter((t: any) => {
      const entryBar = typeof t.entry_bar === 'number' ? t.entry_bar
        : typeof t.entry_time === 'number' ? t.entry_time : null;
      const exitBar = typeof t.exit_bar === 'number' ? t.exit_bar
        : typeof t.exit_time === 'number' ? t.exit_time : null;
      return entryBar != null && currentIndex >= entryBar
        && (exitBar == null || currentIndex < exitBar);
    });
  }, [data?.trades, currentIndex]);

  const closedTrades = useMemo(() => {
    return (data?.trades || [])
      .filter((t: any) => {
        const exitBar = typeof t.exit_bar === 'number' ? t.exit_bar
          : typeof t.exit_time === 'number' ? t.exit_time : null;
        return exitBar != null && currentIndex >= exitBar;
      })
      .slice(-12)
      .reverse();
  }, [data?.trades, currentIndex]);

  // ── Controls ──────────────────────────────────────────────────────────────

  const goToStart = useCallback(() => { setIsPlaying(false); setCurrentIndex(0); }, []);
  const goToEnd = useCallback(() => { setIsPlaying(false); setCurrentIndex(Math.max(0, nBars - 1)); }, [nBars]);
  const stepBack = useCallback(() => { setIsPlaying(false); setCurrentIndex((i) => Math.max(0, i - 1)); }, []);
  const stepForward = useCallback(() => { setIsPlaying(false); setCurrentIndex((i) => Math.min(nBars - 1, i + 1)); }, [nBars]);
  const jump = useCallback((delta: number) => {
    setIsPlaying(false);
    setCurrentIndex((i) => Math.max(0, Math.min(nBars - 1, i + delta)));
  }, [nBars]);
  const togglePlay = useCallback(() => {
    if (currentIndex >= nBars - 1) setCurrentIndex(0);
    setIsPlaying((p) => !p);
  }, [currentIndex, nBars]);

  // Raccourcis clavier (Jinja2 smartreplay) : Espace, ← →, Shift+← → (±10)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.code === 'Space') {
        e.preventDefault();
        togglePlay();
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault();
        jump(e.shiftKey ? -10 : -1);
      } else if (e.code === 'ArrowRight') {
        e.preventDefault();
        jump(e.shiftKey ? 10 : 1);
      } else if (e.code === 'Home') {
        e.preventDefault();
        goToStart();
      } else if (e.code === 'End') {
        e.preventDefault();
        goToEnd();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [togglePlay, jump, goToStart, goToEnd]);

  const currentBarTime = cleanedCandles[currentIndex]?.time;
  const currentPrice = cleanedCandles[currentIndex]?.close;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Smart Replay</h2>
          <p className="text-sm text-muted mt-1">
            Rejeu bougie par bougie · reconstruisez l&apos;état SMC à n&apos;importe quelle barre
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
              aria-label="Symbole"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="w-40 px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              placeholder="BTC/USDC"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Timeframe</label>
            <TimeframeButtons value={timeframe} onChange={setTimeframe} />
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

      {/* Chart full width — hauteur explicite + autoSize */}
      <Card className="p-0 overflow-hidden relative w-full">
        <div
          className="w-full min-h-[560px] h-[min(70vh,720px)]"
          ref={containerRef}
          style={{ width: '100%' }}
        />
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-card/70 z-10">
            <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
          </div>
        )}
        {!isLoading && !isError && cleanedCandles.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-card/70 z-10 text-sm text-muted">
            Aucune bougie pour {symbol} / {timeframe}
          </div>
        )}
      </Card>

      {/* Transport sous le chart */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <Button size="icon" variant="ghost" onClick={goToStart} title="Début (Home)" aria-label="Début">
              <SkipBack className="w-4 h-4" />
            </Button>
            <Button size="sm" variant="ghost" onClick={() => jump(-10)} title="−10 barres (Shift+←)" aria-label="Reculer 10 barres">
              −10
            </Button>
            <Button size="icon" variant="ghost" onClick={stepBack} title="Reculer 1 barre (←)" aria-label="Reculer 1 barre">
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <Button size="icon" variant="primary" onClick={togglePlay} title="Play/Pause (Espace)" aria-label="Play/Pause">
              {isPlaying ? <Pause className="w-4 h-4" fill="currentColor" /> : <Play className="w-4 h-4" fill="currentColor" />}
            </Button>
            <Button size="icon" variant="ghost" onClick={stepForward} title="Avancer 1 barre (→)" aria-label="Avancer 1 barre">
              <ChevronRight className="w-4 h-4" />
            </Button>
            <Button size="sm" variant="ghost" onClick={() => jump(10)} title="+10 barres (Shift+→)" aria-label="Avancer 10 barres">
              +10
            </Button>
            <Button size="icon" variant="ghost" onClick={goToEnd} title="Fin (End)" aria-label="Fin">
              <SkipForward className="w-4 h-4" />
            </Button>
          </div>
          <p className="text-[10px] text-dim hidden md:block">
            Espace · ← → · Shift±10 · Home/End
          </p>

          <div className="flex items-center gap-2">
            <label className="text-xs text-dim">Vitesse</label>
            <select
              aria-label="Vitesse"
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

      {/* Meta bandeau (ex-colonne latérale) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <Activity className="w-3.5 h-3.5 text-primary-400" />
              Structure
            </CardTitle>
          </CardHeader>
          <CardContent>
            {lastStructure ? (
              <div className="flex flex-wrap items-center gap-2">
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
              <div className="text-xs text-muted">Aucune structure</div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Trades ouverts</CardTitle>
            <Badge variant={openTrades.length > 0 ? 'success' : 'default'}>{openTrades.length}</Badge>
          </CardHeader>
          <CardContent className="space-y-2 max-h-40 overflow-y-auto">
            {openTrades.length === 0 ? (
              <div className="text-xs text-muted">Aucun</div>
            ) : (
              openTrades.map((t: any, i: number) => (
                <div key={i} className="text-xs space-y-0.5 border border-border rounded p-2 bg-card-hover">
                  <div className="flex items-center gap-2">
                    <Badge variant={t.side === 'long' ? 'success' : 'danger'}>
                      {t.side?.toUpperCase()}
                    </Badge>
                    <span className="font-mono text-muted">
                      {formatUSD(Number(t.entry ?? t.entry_price ?? 0))}
                    </span>
                  </div>
                  {t.setup && <div className="text-cyan-400 font-mono">{t.setup}</div>}
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <Layers className="w-3.5 h-3.5 text-emerald-400" />
              OBs actifs
            </CardTitle>
            <Badge variant="info">{activeOrderBlocks.length}</Badge>
          </CardHeader>
          <CardContent className="space-y-1 max-h-40 overflow-y-auto">
            {activeOrderBlocks.length === 0 ? (
              <div className="text-xs text-muted">Aucun</div>
            ) : (
              activeOrderBlocks.slice(0, 8).map((ob: any, i: number) => (
                <div key={i} className="flex items-center justify-between text-xs gap-2">
                  <span className={ob.kind === 'bullish' ? 'text-emerald-400' : 'text-red-400'}>
                    {ob.kind === 'bullish' ? 'BULL' : 'BEAR'}
                  </span>
                  <span className="font-mono text-muted truncate">
                    {formatUSD(Number(ob.bottom ?? 0))}–{formatUSD(Number(ob.top ?? 0))}
                  </span>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <Droplets className="w-3.5 h-3.5 text-cyan-400" />
              LPs actifs
            </CardTitle>
            <Badge variant="info">{activeLiquidityPools.length}</Badge>
          </CardHeader>
          <CardContent className="space-y-1 max-h-40 overflow-y-auto">
            {activeLiquidityPools.length === 0 ? (
              <div className="text-xs text-muted">Aucun</div>
            ) : (
              activeLiquidityPools.slice(0, 8).map((lp: any, i: number) => (
                <div key={i} className="flex items-center justify-between text-xs gap-2">
                  <span className={lp.kind === 'buyside' ? 'text-emerald-400' : 'text-red-400'}>
                    {lp.kind === 'buyside' ? 'BUY' : 'SELL'}
                  </span>
                  <span className="font-mono text-muted">{formatUSD(Number(lp.level ?? 0))}</span>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <Waves className="w-3.5 h-3.5 text-amber-400" />
              FVGs ouverts
            </CardTitle>
            <Badge variant="info">{activeFvgs.length}</Badge>
          </CardHeader>
          <CardContent className="space-y-1 max-h-40 overflow-y-auto">
            {activeFvgs.length === 0 ? (
              <div className="text-xs text-muted">Aucun</div>
            ) : (
              activeFvgs.slice(0, 8).map((f: any, i: number) => (
                <div key={i} className="flex items-center justify-between text-xs gap-2">
                  <span className={f.kind === 'bullish' ? 'text-cyan-400' : 'text-amber-400'}>
                    {f.kind === 'bullish' ? 'BULL' : 'BEAR'}
                  </span>
                  <span className="font-mono text-muted truncate">
                    {formatUSD(Number(f.bottom ?? 0))}–{formatUSD(Number(f.top ?? 0))}
                  </span>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {/* Aligné Smart Graph : recommandés (SMC) + réalisés (backtest replay) */}
      <TradePlansTable
        plans={smcData?.trade_plans || []}
        selectedPlan={selectedPlan}
        onSelectPlan={(p) => setSelectedPlan((prev) =>
          prev && prev.entry === p.entry && prev.setup === p.setup ? null : p)}
        title="Trades recommandés"
      />
      <RealizedTradesTable
        trades={(data?.trades || closedTrades) as any}
        strategy="smart_money"
      />
      <p className="text-[11px] text-dim">
        <strong className="text-muted">Pourquoi N plans vs 1 trade ?</strong>{' '}
        Les <em>recommandés</em> sont des setups SMC détectés (zones + score) —
        pas des ordres exécutés. Les <em>réalisés</em> viennent du backtester
        smart_money (filtres score/RR/HTF) : souvent beaucoup moins nombreux.
        Sur 15m–1h et actions, l&apos;historique Yahoo est court (~88 bougies) :
        le replay produit peu de trades.
      </p>
    </div>
  );
}
