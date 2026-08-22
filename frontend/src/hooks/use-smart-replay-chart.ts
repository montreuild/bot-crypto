'use client';

/**
 * Graphique du Smart Replay : cycle de vie et redessin causal (ARCH-02).
 *
 * Extrait de `smart-replay-view.tsx`, sur le modèle de `use-smart-graph-chart`.
 * La vue n'a plus qu'à fournir l'état de rejeu et poser le conteneur rendu.
 */
import { useEffect, useRef } from 'react';
import {
  ColorType, LineStyle, createChart,
  type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from 'lightweight-charts';

import { resolveSignalTimestamp } from '@/components/views/smart-graph-helpers';
import {
  entityAliveAt, entityBarIndex, entityEndIndex, tradeBars,
} from '@/components/views/smart-replay-entities';
import type { CandleRow } from '@/lib/ohlcv';
import {
  ZonesPrimitive, buildZonesFromSmc, ensureZonesPrimitive,
} from '@/lib/smc-zones';

export interface ReplayLevels {
  entry?: number;
  stop?: number;
  tp?: number;
  setup?: string;
  signal_time?: number | null;
}

interface Params {
  data: any;
  candles: CandleRow[];
  currentIndex: number;
  selectedPlan: ReplayLevels | null;
}

const THEME = {
  layout: {
    background: { type: ColorType.Solid, color: '#0f1419' },
    textColor: '#9ca3af',
    fontFamily: 'var(--font-jetbrains), monospace',
  },
  grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
  timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#1f2937' },
  crosshair: { mode: 1 as const },
  rightPriceScale: { borderColor: '#1f2937' },
};

const BOUGIE = {
  upColor: '#10b981', downColor: '#ef4444',
  borderUpColor: '#10b981', borderDownColor: '#ef4444',
  wickUpColor: '#10b981', wickDownColor: '#ef4444',
};

export function useSmartReplayChart({ data, candles, currentIndex, selectedPlan }: Params) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlaysRef = useRef<ISeriesApi<any>[]>([]);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);
  const zonesPrimRef = useRef<ZonesPrimitive | null>(null);

  // autoSize + hauteur explicite (même correctif que Smart Graph).
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const chart = createChart(el, { autoSize: true, ...THEME });
    const candleSeries = chart.addCandlestickSeries(BOUGIE);
    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    zonesPrimRef.current = ensureZonesPrimitive(candleSeries as any, null);

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) chart.applyOptions({ width, height });
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

  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;

    const sliceEnd = Math.max(1, Math.min(currentIndex + 1, candles.length));
    candleSeries.setData(candles.slice(0, sliceEnd));

    for (const s of overlaysRef.current) {
      try { chart.removeSeries(s); } catch { /* déjà retirée */ }
    }
    overlaysRef.current = [];
    for (const pl of priceLinesRef.current) {
      try { candleSeries.removePriceLine(pl); } catch { /* déjà retirée */ }
    }
    priceLinesRef.current = [];

    if (!data || candles.length === 0) {
      candleSeries.setMarkers([]);
      zonesPrimRef.current?.setZones([]);
      return;
    }

    const lastVisibleTime = candles[sliceEnd - 1].time as number;
    const timeAt = (idx: number | null | undefined): number | null => {
      if (idx == null || !Number.isFinite(idx)) return null;
      const i = Math.max(0, Math.min(Math.floor(idx), candles.length - 1));
      return (candles[i]?.time as number) ?? null;
    };

    // ── Zones remplies, bornées à la barre courante ─────────────────────────
    const prim = zonesPrimRef.current;
    if (prim) {
      const toTimed = (list: any[] | undefined) =>
        (list || [])
          .filter((e: any) => entityAliveAt(e, currentIndex))
          .map((e: any) => {
            const t1 = timeAt(entityBarIndex(e)) ?? (e.time_start as number);
            let t2 = timeAt(entityEndIndex(e)) ?? (e.time_end as number) ?? lastVisibleTime;
            if (t2 > lastVisibleTime) t2 = lastVisibleTime;
            return { ...e, time_start: t1, time_end: t2 };
          })
          .filter((e: any) => e.time_start != null && e.time_end != null);
      prim.setZones(buildZonesFromSmc({
        order_blocks: toTimed(data.order_blocks),
        fvgs: toTimed(data.fvgs),
        liquidity_voids: toTimed(data.voids || data.liquidity_voids),
        breakers: toTimed(data.breakers),
        rejection_blocks: toTimed(data.rejections || data.rejection_blocks),
      }, {
        orderBlocks: true, fvg: true, liquidityVoids: true,
        breakers: true, rejectionBlocks: true,
        firstTime: candles[0]?.time as number,
        lastTime: lastVisibleTime,
      }));
    }

    // ── Pools de liquidité — ligne de prix horizontale ──────────────────────
    const pools = data.liquidity_pools || data.pools;
    if (Array.isArray(pools)) {
      for (const lp of pools) {
        if (!entityAliveAt(lp, currentIndex)) continue;
        priceLinesRef.current.push(candleSeries.createPriceLine({
          price: lp.level,
          color: lp.kind === 'buyside' ? 'rgba(16, 185, 129, 0.85)' : 'rgba(239, 68, 68, 0.85)',
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: `LP ${lp.kind}`,
        }));
      }
    }

    // ── Niveaux Entry / SL / TP, tirés jusqu'à la dernière barre visible ────
    const visibleTimes = candles.slice(0, sliceEnd).map((c) => c.time as number);
    const lastVisible = visibleTimes.length
      ? (visibleTimes[visibleTimes.length - 1] as UTCTimestamp) : null;
    const addLevel = (
      from: UTCTimestamp | null, price: number, color: string,
      width: 1 | 2 | 3 | 4, title: string,
    ) => {
      if (from == null || lastVisible == null
          || !Number.isFinite(price) || price <= 0
          || (lastVisible as number) < (from as number)) return;
      const s = chart.addLineSeries({
        color, lineWidth: width, lineStyle: LineStyle.Solid,
        priceLineVisible: true, lastValueVisible: true,
        crosshairMarkerVisible: false, title,
      });
      s.setData([{ time: from, value: price }, { time: lastVisible, value: price }]);
      overlaysRef.current.push(s);
    };

    for (const t of (data.trades || [])) {
      const { entry, exit } = tradeBars(t);
      const isOpen = entry != null && currentIndex >= entry
        && (exit == null || currentIndex < exit);
      if (!isOpen) continue;
      const from = (timeAt(entry)
        ?? resolveSignalTimestamp(visibleTimes, t.signal_time)) as UTCTimestamp | null;
      addLevel(from, Number(t.entry ?? t.entry_price), '#0ea5e9', 3,
               `Entry ${t.side === 'long' ? 'LONG' : 'SHORT'}`);
      addLevel(from, Number(t.stop), '#f87171', 2, 'SL');
      addLevel(from, Number(t.tp ?? t.take_profit), '#34d399', 2, 'TP');
    }

    // Plan cliqué dans les tables — mêmes trois lignes, depuis le signal.
    if (selectedPlan && lastVisible != null) {
      const from = resolveSignalTimestamp(
        visibleTimes, selectedPlan.signal_time) as UTCTimestamp | null;
      addLevel(from, Number(selectedPlan.entry), '#0ea5e9', 4,
               `Entry ${selectedPlan.setup || ''}`.trim());
      addLevel(from, Number(selectedPlan.stop), '#f87171', 3, 'SL');
      addLevel(from, Number(selectedPlan.tp), '#34d399', 3, 'TP');
    }

    // ── Marqueurs de structure ──────────────────────────────────────────────
    const markers: SeriesMarker<Time>[] = [];
    const pousser = (list: any[] | undefined, faire: (e: any) => SeriesMarker<Time>) => {
      for (const e of (list || [])) {
        const idx = entityBarIndex(e, ['index', 'created_at']);
        if (idx != null && currentIndex < idx) continue;
        markers.push(faire(e));
      }
    };
    pousser(data.structure?.bos, (b) => ({
      time: b.time as UTCTimestamp,
      position: b.type === 'bullish' ? 'belowBar' : 'aboveBar',
      color: b.type === 'bullish' ? '#10b981' : '#ef4444',
      shape: b.type === 'bullish' ? 'arrowUp' : 'arrowDown',
      text: 'BOS',
    }));
    pousser(data.structure?.choch, (c) => ({
      time: c.time as UTCTimestamp,
      position: c.type === 'bullish' ? 'belowBar' : 'aboveBar',
      color: '#22d3ee', shape: 'circle', text: 'CHoCH',
    }));
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    candleSeries.setMarkers(markers);

    chart.timeScale().scrollToRealTime();
  }, [data, currentIndex, candles, selectedPlan]);

  return { containerRef };
}
