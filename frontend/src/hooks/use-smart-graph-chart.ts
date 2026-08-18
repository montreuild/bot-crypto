'use client';

import { useEffect, useRef } from 'react';
import {
  createChart, ColorType, LineStyle,
  type IChartApi, type ISeriesApi, type UTCTimestamp, type Time, type SeriesMarker,
} from 'lightweight-charts';
import {
  ZonesPrimitive,
  ensureZonesPrimitive,
  buildZonesFromSmc,
  type SmcZone,
} from '@/lib/smc-zones';
import { cleanOhlcv } from '@/lib/ohlcv';
import {
  normalizePd, type OverlayToggles, type ChartIndicators, type SeriesPoint,
} from '@/components/views/smart-graph-helpers';
import type { TradePlan } from '@/components/cards/trade-plans-table';
import type { SmcChartData } from '@/types';

export function useSmartGraphChart(opts: {
  data: SmcChartData | undefined;
  selectedPlan: TradePlan | null;
  toggles: OverlayToggles;
  indicators: ChartIndicators | null;
  showEma: boolean;
  showBb: boolean;
  showRsi: boolean;
  showMacd: boolean;
}) {
  const { data, selectedPlan, toggles, indicators, showEma, showBb, showRsi, showMacd } = opts;

const containerRef = useRef<HTMLDivElement>(null);
const chartRef = useRef<IChartApi | null>(null);
const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
const overlaysRef = useRef<ISeriesApi<any>[]>([]);
const priceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);
const planLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);
const planSeriesRef = useRef<ISeriesApi<any>[]>([]);
const zonesPrimRef = useRef<ZonesPrimitive | null>(null);
// EMA / BB (sur le chart principal) + RSI / MACD (panneaux dédiés)
const indSeriesRef = useRef<ISeriesApi<any>[]>([]);
const rsiContainerRef = useRef<HTMLDivElement>(null);
const macdContainerRef = useRef<HTMLDivElement>(null);
const rsiChartRef = useRef<IChartApi | null>(null);
const macdChartRef = useRef<IChartApi | null>(null);

// Create chart once — autoSize pour un dimensionnement correct du conteneur.
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
    upColor: '#10b981', downColor: '#ef4444',
    borderUpColor: '#10b981', borderDownColor: '#ef4444',
    wickUpColor: '#10b981', wickDownColor: '#ef4444',
  });
  chartRef.current = chart;
  candleSeriesRef.current = candleSeries;
  zonesPrimRef.current = ensureZonesPrimitive(candleSeries as any, null);

  // Filet de sécurité si autoSize n'est pas disponible / conteneur 0.
  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) {
        chart.applyOptions({ width, height });
      }
    }
  });
  ro.observe(el);

  // Synchronise la plage temporelle des panneaux RSI/MACD sur le chart
  // principal (zoom/scroll) — panneaux créés/détruits dynamiquement
  // (toggle), donc lus via ref à l'appel plutôt que capturés. Pas de
  // désinscription explicite : `chart.remove()` en cleanup suffit (même
  // pattern que le multi-panel de Scanner).
  chart.timeScale().subscribeVisibleTimeRangeChange(() => {
    const range = chart.timeScale().getVisibleRange();
    if (!range) return;
    try { rsiChartRef.current?.timeScale().setVisibleRange(range); } catch { /* hors domaine */ }
    try { macdChartRef.current?.timeScale().setVisibleRange(range); } catch { /* hors domaine */ }
  });

  return () => {
    ro.disconnect();
    chart.remove();
    chartRef.current = null;
    candleSeriesRef.current = null;
    zonesPrimRef.current = null;
    overlaysRef.current = [];
    priceLinesRef.current = [];
    planLinesRef.current = [];
    indSeriesRef.current = [];
  };
}, []);

// EMA / BB — lignes superposées sur le chart principal (comme Scanner)
useEffect(() => {
  const chart = chartRef.current;
  if (!chart) return;
  for (const s of indSeriesRef.current) {
    try { chart.removeSeries(s); } catch { /* noop */ }
  }
  indSeriesRef.current = [];
  if (!indicators) return;
  const addLine = (arr: SeriesPoint[], color: string, width: 1 | 2 = 1) => {
    if (!arr?.length) return;
    const s = chart.addLineSeries({
      color, lineWidth: width,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    s.setData(arr.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    indSeriesRef.current.push(s);
  };
  if (showEma) {
    addLine(indicators.ema20 || [], '#22d3ee');
    addLine(indicators.ema50 || [], '#a78bfa');
    addLine(indicators.ema200 || [], '#f59e0b', 2);
  }
  if (showBb) {
    addLine(indicators.bb_upper || [], 'rgba(148,163,184,.7)');
    addLine(indicators.bb_mid || [], 'rgba(148,163,184,.4)');
    addLine(indicators.bb_lower || [], 'rgba(148,163,184,.7)');
  }
}, [indicators, showEma, showBb]);

// RSI — panneau dédié, créé/détruit avec le toggle
useEffect(() => {
  if (!showRsi || !rsiContainerRef.current) {
    if (rsiChartRef.current) { rsiChartRef.current.remove(); rsiChartRef.current = null; }
    return;
  }
  const chart = createChart(rsiContainerRef.current, {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: '#0f1419' },
      textColor: '#9ca3af',
      fontFamily: 'var(--font-jetbrains), monospace',
    },
    grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
    timeScale: { borderColor: '#1f2937', timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: '#1f2937' },
  });
  rsiChartRef.current = chart;
  return () => { chart.remove(); rsiChartRef.current = null; };
}, [showRsi]);

useEffect(() => {
  const chart = rsiChartRef.current;
  if (!chart || !indicators) return;
  const rsiData = (indicators.rsi || []).map((p: SeriesPoint) => ({ time: p.time as UTCTimestamp, value: p.value }));
  if (!rsiData.length) return;
  const s = chart.addLineSeries({
    color: '#e879f9', lineWidth: 2,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  s.setData(rsiData);
  s.createPriceLine({ price: 70, color: 'rgba(239,68,68,.5)', lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
  s.createPriceLine({ price: 30, color: 'rgba(16,185,129,.5)', lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
  return () => { try { chart.removeSeries(s); } catch { /* noop */ } };
}, [indicators, showRsi]);

// MACD — panneau dédié, créé/détruit avec le toggle
useEffect(() => {
  if (!showMacd || !macdContainerRef.current) {
    if (macdChartRef.current) { macdChartRef.current.remove(); macdChartRef.current = null; }
    return;
  }
  const chart = createChart(macdContainerRef.current, {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: '#0f1419' },
      textColor: '#9ca3af',
      fontFamily: 'var(--font-jetbrains), monospace',
    },
    grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
    timeScale: { borderColor: '#1f2937', timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: '#1f2937' },
  });
  macdChartRef.current = chart;
  return () => { chart.remove(); macdChartRef.current = null; };
}, [showMacd]);

useEffect(() => {
  const chart = macdChartRef.current;
  if (!chart || !indicators) return;
  const series: ISeriesApi<any>[] = [];
  const macdData = (indicators.macd || []).map((p: SeriesPoint) => ({ time: p.time as UTCTimestamp, value: p.value }));
  const signalData = (indicators.macd_signal || []).map((p: SeriesPoint) => ({ time: p.time as UTCTimestamp, value: p.value }));
  const histData = (indicators.macd_hist || []).map((p: SeriesPoint) => ({
    time: p.time as UTCTimestamp,
    value: p.value,
    color: p.value >= 0 ? 'rgba(16,185,129,.5)' : 'rgba(239,68,68,.5)',
  }));
  if (macdData.length) {
    const s = chart.addLineSeries({
      color: '#22d3ee', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    s.setData(macdData);
    series.push(s);
  }
  if (signalData.length) {
    const s = chart.addLineSeries({
      color: '#f59e0b', lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    s.setData(signalData);
    series.push(s);
  }
  if (histData.length) {
    const hist = chart.addHistogramSeries({ priceLineVisible: false });
    hist.setData(histData);
    series.push(hist);
  }
  return () => {
    for (const s of series) {
      try { chart.removeSeries(s); } catch { /* noop */ }
    }
  };
}, [indicators, showMacd]);

// Candles
useEffect(() => {
  if (!candleSeriesRef.current || !data?.ohlcv) return;
  const ohlcv = data.ohlcv;
  const cleaned = cleanOhlcv(
    ohlcv.time || [], ohlcv.open || [], ohlcv.high || [], ohlcv.low || [], ohlcv.close || [],
  );
  candleSeriesRef.current.setData(cleaned);
  chartRef.current?.timeScale().fitContent();
}, [data?.ohlcv]);

useEffect(() => {
  const chart = chartRef.current;
  const candleSeries = candleSeriesRef.current;
  if (!chart || !candleSeries || !data) return;

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

  // Zones remplies (OB / FVG / voids / breakers / rejections) via primitive canvas
  if (zonesPrimRef.current) {
    const filled: SmcZone[] = buildZonesFromSmc(data, {
      orderBlocks: toggles.orderBlocks,
      fvg: toggles.fvg,
      liquidityVoids: toggles.liquidityVoids,
      breakers: toggles.breakers,
      rejectionBlocks: toggles.rejectionBlocks,
      firstTime: firstTime as number,
      lastTime: lastTime as number,
    });
    zonesPrimRef.current.setZones(filled);
  }

  if (toggles.trendlines && Array.isArray(data.trendlines)) {
    for (const tl of data.trendlines) {
      const color = tl.kind === 'support' ? 'rgba(16, 185, 129, 0.95)' : 'rgba(239, 68, 68, 0.95)';
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

  if (toggles.channel && data.channel) {
    const ch = data.channel;
    const ts = (ch.time_start ?? firstTime) as UTCTimestamp;
    const te = (ch.time_end ?? lastTime) as UTCTimestamp;
    const hw = Number(ch.half_width || 0);
    const midSeries = chart.addLineSeries({
      color: 'rgba(139, 92, 246, 0.9)', lineWidth: 2, lineStyle: LineStyle.Solid,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    midSeries.setData([
      { time: ts, value: Number(ch.mid_start) },
      { time: te, value: Number(ch.mid_end) },
    ]);
    const upperSeries = chart.addLineSeries({
      color: 'rgba(139, 92, 246, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    upperSeries.setData([
      { time: ts, value: Number(ch.mid_start) + hw },
      { time: te, value: Number(ch.mid_end) + hw },
    ]);
    const lowerSeries = chart.addLineSeries({
      color: 'rgba(139, 92, 246, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    lowerSeries.setData([
      { time: ts, value: Number(ch.mid_start) - hw },
      { time: te, value: Number(ch.mid_end) - hw },
    ]);
    overlaysRef.current.push(midSeries, upperSeries, lowerSeries);
  }

  if (toggles.structureLine && Array.isArray(data.structure_line)) {
    const sl = data.structure_line;
    if (sl.length >= 2) {
      const zigSeries = chart.addLineSeries({
        color: 'rgba(156, 163, 175, 0.7)', lineWidth: 1, lineStyle: LineStyle.Solid,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      zigSeries.setData(
        sl.map((pt) => ({
          time: pt.time as UTCTimestamp,
          value: Number(pt.price),
        })).filter((p) => p.time != null),
      );
      overlaysRef.current.push(zigSeries);
    }
  }

  if (toggles.cycle && data.cycle) {
    const cy = data.cycle;
    if (cy.from_time != null && cy.target != null && Number.isFinite(Number(cy.target))) {
      const projSeries = chart.addLineSeries({
        color: 'rgba(245, 158, 11, 0.9)', lineWidth: 2, lineStyle: LineStyle.LargeDashed,
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
          color: 'rgba(245, 158, 11, 0.7)',
          lineStyle: LineStyle.Dashed, lineWidth: 1,
          axisLabelVisible: true, title: 'Cycle target',
        }),
      );
    }
  }

  if (toggles.liquidityPools && Array.isArray(data.liquidity_pools)) {
    for (const lp of data.liquidity_pools) {
      const buyside = lp.kind === 'buyside';
      const color = buyside ? 'rgba(16, 185, 129, 0.9)' : 'rgba(239, 68, 68, 0.9)';
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: Number(lp.level),
          color,
          lineStyle: LineStyle.Dashed, lineWidth: 1,
          axisLabelVisible: true,
          title: `LP ${lp.kind}${lp.status === 'swept' ? ' ✕' : ''}`,
        }),
      );
    }
  }

  if (toggles.volumeProfile && data.volume_profile) {
    const vp = data.volume_profile;
    if (vp.poc != null) {
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: Number(vp.poc),
          color: 'rgba(34, 211, 238, 0.95)',
          lineStyle: LineStyle.Solid, lineWidth: 2,
          axisLabelVisible: true, title: 'POC',
        }),
      );
    }
    for (const hvn of (vp.hvns || [])) {
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: Number(hvn),
          color: 'rgba(34, 211, 238, 0.45)',
          lineStyle: LineStyle.Dashed, lineWidth: 1,
          axisLabelVisible: false, title: 'HVN',
        }),
      );
    }
    for (const lvn of (vp.lvns || [])) {
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: Number(lvn),
          color: 'rgba(245, 158, 11, 0.45)',
          lineStyle: LineStyle.Dotted, lineWidth: 1,
          axisLabelVisible: false, title: 'LVN',
        }),
      );
    }
  }

  if (toggles.premiumDiscount) {
    const pdn = normalizePd(data.premium_discount);
    if (pdn?.high != null) {
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: pdn.high,
          color: 'rgba(239, 68, 68, 0.85)',
          lineStyle: LineStyle.Dotted, lineWidth: 1,
          axisLabelVisible: true, title: 'Premium',
        }),
      );
    }
    if (pdn?.equilibrium != null) {
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: pdn.equilibrium,
          color: 'rgba(156, 163, 175, 0.85)',
          lineStyle: LineStyle.Dashed, lineWidth: 1,
          axisLabelVisible: true, title: 'Equilibrium',
        }),
      );
    }
    if (pdn?.low != null) {
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: pdn.low,
          color: 'rgba(16, 185, 129, 0.85)',
          lineStyle: LineStyle.Dotted, lineWidth: 1,
          axisLabelVisible: true, title: 'Discount',
        }),
      );
    }
  }

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

// Plan sélectionné → SL / TP (traits pleins) + Entry depuis la bougie signal
useEffect(() => {
  const chart = chartRef.current;
  const candleSeries = candleSeriesRef.current;
  if (!candleSeries) return;

  for (const pl of planLinesRef.current) {
    try { candleSeries.removePriceLine(pl); } catch { /* noop */ }
  }
  planLinesRef.current = [];
  if (chart) {
    for (const s of planSeriesRef.current) {
      try { chart.removeSeries(s); } catch { /* noop */ }
    }
  }
  planSeriesRef.current = [];

  if (!selectedPlan || !chart) return;
  const entry = Number(selectedPlan.entry);
  const stop = Number(selectedPlan.stop);
  const tp = Number(selectedPlan.tp);
  const isLong = selectedPlan.side === 'long';
  const times: number[] = data?.ohlcv?.time || [];
  const lastTime = times.length ? (times[times.length - 1] as UTCTimestamp) : null;

  // Bougie de signal : exacte, sinon 1re bougie ≥ signal_time, sinon dernière.
  let signalTs: UTCTimestamp | null = null;
  const rawSig = Number(selectedPlan.signal_time);
  if (times.length > 0) {
    if (Number.isFinite(rawSig) && rawSig > 0) {
      if (times.includes(rawSig)) {
        signalTs = rawSig as UTCTimestamp;
      } else {
        const after = times.find((t) => t >= rawSig);
        signalTs = (after ?? times[times.length - 1]) as UTCTimestamp;
      }
    } else {
      signalTs = times[times.length - 1] as UTCTimestamp;
    }
  }

  // SL / TP : traits pleins, bien visibles (price lines pleine largeur)
  if (Number.isFinite(stop) && stop > 0) {
    planLinesRef.current.push(
      candleSeries.createPriceLine({
        price: stop,
        color: '#f87171',
        lineStyle: LineStyle.Solid,
        lineWidth: 3,
        axisLabelVisible: true,
        title: 'SL',
      }),
    );
  }
  if (Number.isFinite(tp) && tp > 0) {
    planLinesRef.current.push(
      candleSeries.createPriceLine({
        price: tp,
        color: '#34d399',
        lineStyle: LineStyle.Solid,
        lineWidth: 3,
        axisLabelVisible: true,
        title: 'TP',
      }),
    );
  }

  // Entry : commence à la bougie du signal (pas avant) → série limitée
  if (
    Number.isFinite(entry) && entry > 0
    && signalTs != null && lastTime != null
    && (lastTime as number) >= (signalTs as number)
  ) {
    // Ligne d'entrée bien visible (bleu vif, épaisseur 4)
    const entrySeries = chart.addLineSeries({
      color: '#0ea5e9',
      lineWidth: 4,
      lineStyle: LineStyle.Solid,
      priceLineVisible: true,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      title: `Entry ${selectedPlan.setup || ''}`.trim(),
    });
    entrySeries.setData([
      { time: signalTs, value: entry },
      { time: lastTime, value: entry },
    ]);
    planSeriesRef.current.push(entrySeries);
    // Point d'ancrage signal
    const anchor = chart.addLineSeries({
      color: '#38bdf8',
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    });
    anchor.setData([{ time: signalTs, value: entry }]);
    planSeriesRef.current.push(anchor);
  }

  // Zone d'entrée depuis le signal uniquement
  const zl = Number(selectedPlan.zone_low);
  const zh = Number(selectedPlan.zone_high);
  if (
    signalTs != null && lastTime != null
    && Number.isFinite(zl) && Number.isFinite(zh) && zl > 0 && zh > 0
    && (lastTime as number) >= (signalTs as number)
  ) {
    const color = isLong ? 'rgba(56, 189, 248, 0.85)' : 'rgba(251, 113, 133, 0.85)';
    for (const value of [zh, zl]) {
      const series = chart.addLineSeries({
        color,
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      series.setData([
        { time: signalTs, value },
        { time: lastTime, value },
      ]);
      planSeriesRef.current.push(series);
    }
  }
}, [selectedPlan, data?.ohlcv?.time]);


  return { containerRef, rsiContainerRef, macdContainerRef };
}
