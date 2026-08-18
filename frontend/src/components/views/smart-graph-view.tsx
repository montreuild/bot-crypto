'use client';

/**
 * Onglet Smart Graph de `/market` — chart candlestick full-width + overlays SMC.
 * Meta (signal, bias, PD…) en bandeau sous « Plans recommandés ».
 */

import { useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, errorMessage } from '@/lib/utils';
import { useSMC, useSMCReplay } from '@/hooks/use-api';
import {
  TradePlansTable, RealizedTradesTable, type TradePlan, type RealizedTrade,
} from '@/components/cards/trade-plans-table';
import { FastAnalysisPanel, type FastAnalysisResult } from '@/components/cards/fast-analysis-panel';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import {
  Loader2, RefreshCw, AlertCircle, Activity, Search,
  ArrowUp, ArrowDown, Target, Shield, Layers, Droplets, Waves, GitBranch, Sparkles,
  Box, Ban, BarChart3, TrendingUp, Recycle, Spline, Flame, CircleDot,
} from 'lucide-react';
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
// F2 — cleanOhlcv factorisé dans lib/ohlcv.ts (avant : copie locale)
import { cleanOhlcv, type CandleRow } from '@/lib/ohlcv';
import {
  formatPrice, normalizePd, type OverlayToggles, type ChartIndicators, type SeriesPoint,
} from '@/components/views/smart-graph-helpers';
import { SmartGraphTables } from '@/components/views/smart-graph-tables';

export function SmartGraphView({
  initialSymbol,
  initialTf,
}: {
  initialSymbol?: string;
  initialTf?: string;
} = {}) {
  const { defaultTf } = useTradingTimeframes(initialTf || '1h');
  const [symbol, setSymbol] = useState(initialSymbol || 'BTC/USDC');
  const [timeframe, setTimeframe] = useState<string>(initialTf || defaultTf);
  const [selectedPlan, setSelectedPlan] = useState<TradePlan | null>(null);
  const [faResult, setFaResult] = useState<FastAnalysisResult | null>(null);
  const [faLoading, setFaLoading] = useState(false);
  const [showEma, setShowEma] = useState(true);
  const [showBb, setShowBb] = useState(true);
  const [showRsi, setShowRsi] = useState(true);
  const [showMacd, setShowMacd] = useState(true);
  const [indicators, setIndicators] = useState<ChartIndicators | null>(null);

  // URL / props : bascule symbole + TF (clic Top opportunités)
  useEffect(() => {
    if (initialSymbol) setSymbol(initialSymbol);
  }, [initialSymbol]);
  useEffect(() => {
    if (initialTf) setTimeframe(initialTf);
    else setTimeframe((tf) => tf || defaultTf);
  }, [initialTf, defaultTf]);
  const [toggles, setToggles] = useState<OverlayToggles>({
    orderBlocks: true,
    liquidityPools: true,
    fvg: true,
    liquidityVoids: true,
    breakers: false,
    rejectionBlocks: true,
    trendlines: true,
    channel: false,
    structure: true,
    swingLabels: false,
    structureLine: false,
    premiumDiscount: true,
    volumeProfile: false,
    cycle: false,
  });

  const { data, isLoading, isError, isFetching, refetch, error } = useSMC(symbol, timeframe, 2000);
  // Trades réalisés = backtest smart_money (même moteur que Smart Replay)
  const { data: replayData, isLoading: replayLoading } = useSMCReplay(symbol, timeframe, 800);

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

  // Reset plan sélectionné au changement symbole/TF
  useEffect(() => {
    setSelectedPlan(null);
  }, [symbol, timeframe]);

  // Indicateurs EMA/BB/RSI/MACD — même endpoint que le chart Scanner.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await api.getScannerChart(symbol, timeframe, 500);
        if (!cancelled) setIndicators(payload?.indicators || null);
      } catch {
        if (!cancelled) setIndicators(null);
      }
    })();
    return () => { cancelled = true; };
  }, [symbol, timeframe]);

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
    const rsiData = (indicators.rsi || []).map((p: any) => ({ time: p.time as UTCTimestamp, value: p.value }));
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
    const macdData = (indicators.macd || []).map((p: any) => ({ time: p.time as UTCTimestamp, value: p.value }));
    const signalData = (indicators.macd_signal || []).map((p: any) => ({ time: p.time as UTCTimestamp, value: p.value }));
    const histData = (indicators.macd_hist || []).map((p: any) => ({
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

  /** Zone SMC : bords + milieu pour plus de lisibilité. */
  function drawZone(
    chart: IChartApi,
    ts: UTCTimestamp,
    te: UTCTimestamp,
    top: number,
    bottom: number,
    color: string,
    opts?: { lineStyle?: LineStyle; lineWidth?: 1 | 2 | 3 | 4; mid?: boolean },
  ) {
    if (te < ts || !Number.isFinite(top) || !Number.isFinite(bottom)) return;
    const lineStyle = opts?.lineStyle ?? LineStyle.Solid;
    const lineWidth = (opts?.lineWidth ?? 2) as 1 | 2 | 3 | 4;
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
    if (opts?.mid !== false) {
      const mid = (top + bottom) / 2;
      const midSeries = chart.addLineSeries({
        color, lineWidth: 1, lineStyle: LineStyle.Dotted,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      midSeries.setData([{ time: ts, value: mid }, { time: te, value: mid }]);
      overlaysRef.current.push(midSeries);
    }
  }

  // Overlays SMC
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
          sl.map((pt: any) => ({
            time: pt.time as UTCTimestamp,
            value: Number(pt.price),
          })).filter((p: any) => p.time != null),
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

  const handleRefresh = async () => {
    try {
      await refetch();
      toast.success('Données SMC rafraîchies');
    } catch (e) {
      toast.error(`Erreur: ${errorMessage(e)}`);
    }
  };

  const runFastAnalysis = async () => {
    setFaLoading(true);
    try {
      const r = await api.fastAnalysis(symbol, timeframe);
      setFaResult(r);
      toast.success(`Fast Analyse ${symbol} ${timeframe}`);
    } catch (e) {
      toast.error(errorMessage(e, 'Fast Analyse impossible'));
      setFaResult({ error: errorMessage(e, 'erreur') });
    } finally {
      setFaLoading(false);
    }
  };

  const handleSelectPlan = (plan: TradePlan) => {
    setSelectedPlan((prev) => {
      // Re-clic = désélection
      if (prev && prev.entry === plan.entry && prev.stop === plan.stop && prev.setup === plan.setup) {
        toast.message('Plan désélectionné');
        return null;
      }
      toast.success(
        `Plan ${String(plan.side || '').toUpperCase()} ${plan.setup || ''} — Entry / SL / TP affichés`,
      );
      return plan;
    });
  };

  /** Trade réalisé → même overlay Entry/SL/TP que les plans recommandés. */
  const handleSelectRealized = (t: RealizedTrade) => {
    const plan: TradePlan = {
      side: t.side,
      setup: t.setup || 'realized',
      entry: Number(t.entry ?? t.entry_price),
      stop: Number(t.stop),
      tp: Number(t.tp ?? t.take_profit),
      signal_time: t.signal_time ?? null,
      reason: t.exit_reason || t.reason,
      status: 'immediate',
    };
    setSelectedPlan((prev) => {
      if (
        prev
        && prev.entry === plan.entry
        && prev.stop === plan.stop
        && prev.setup === plan.setup
        && prev.signal_time === plan.signal_time
      ) {
        toast.message('Trade désélectionné');
        return null;
      }
      toast.success(
        `Trade ${String(plan.side || '').toUpperCase()} — Entry / SL / TP affichés`,
      );
      return plan;
    });
  };

  const signal = data?.signal;
  const htfBias = data?.htf_bias;
  const pd = normalizePd(data?.premium_discount);
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
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Smart Graph SMC</h2>
          <p className="text-sm text-muted mt-1">
            Chart full-width · cliquez un plan pour afficher Entry / SL / TP
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={runFastAnalysis} disabled={faLoading} variant="outline">
            {faLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            Fast Analyse
          </Button>
          <Button onClick={handleRefresh} disabled={isFetching} variant="primary">
            {isFetching ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </Button>
        </div>
      </div>

      {faResult && (
        <FastAnalysisPanel result={faResult} symbol={symbol} timeframe={timeframe} />
      )}

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
            <TimeframeButtons value={timeframe} onChange={setTimeframe} />
          </div>
          <div className="flex-1" />
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
          <div className="w-full flex flex-wrap gap-3 pt-2 border-t border-border">
            <span className="text-[10px] text-dim uppercase tracking-wider self-center">Indicateurs</span>
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={showEma} onChange={(e) => setShowEma(e.target.checked)} className="rounded" />
              EMA
            </label>
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={showBb} onChange={(e) => setShowBb(e.target.checked)} className="rounded" />
              BB
            </label>
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={showRsi} onChange={(e) => setShowRsi(e.target.checked)} className="rounded" />
              RSI
            </label>
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={showMacd} onChange={(e) => setShowMacd(e.target.checked)} className="rounded" />
              MACD
            </label>
          </div>
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

      {/* Chart full width — hauteur fixe + autoSize */}
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
        {!isLoading && !isError && data && !(data.ohlcv?.time?.length) && (
          <div className="absolute inset-0 flex items-center justify-center bg-card/70 z-10 text-sm text-muted">
            Aucune bougie reçue pour {symbol} / {timeframe}
          </div>
        )}
        {selectedPlan && (
          <div className="absolute top-2 left-2 z-10 flex flex-wrap gap-1.5 text-[10px]">
            <Badge variant="info">Plan {String(selectedPlan.side || '').toUpperCase()} {selectedPlan.setup}</Badge>
            <Badge variant="default">Entry {formatPrice(selectedPlan.entry)}</Badge>
            <Badge variant="danger">SL {formatPrice(selectedPlan.stop)}</Badge>
            <Badge variant="success">TP {formatPrice(selectedPlan.tp)}</Badge>
            <button
              type="button"
              className="px-1.5 py-0.5 rounded bg-card/90 border border-border text-muted hover:text-foreground"
              onClick={() => setSelectedPlan(null)}
            >
              ✕
            </button>
          </div>
        )}
      </Card>

      {/* Panneaux RSI / MACD — sélectionnables comme EMA/BB, mêmes données que Scanner */}
      {showRsi && (
        <Card className="p-0 overflow-hidden">
          <div className="text-[9px] text-dim px-2 pt-1">RSI</div>
          <div ref={rsiContainerRef} className="w-full h-[100px]" />
        </Card>
      )}
      {showMacd && (
        <Card className="p-0 overflow-hidden">
          <div className="text-[9px] text-dim px-2 pt-1">MACD</div>
          <div ref={macdContainerRef} className="w-full h-[110px]" />
        </Card>
      )}

      {!isError && (
        <>
          {isLoading ? (
            <Card>
              <CardContent className="flex items-center justify-center gap-2 py-8 text-sm text-muted">
                <Loader2 className="w-5 h-5 animate-spin text-primary-400" />
                Chargement des trades recommandés…
              </CardContent>
            </Card>
          ) : (
            <TradePlansTable
              plans={data?.trade_plans || []}
              onSelectPlan={handleSelectPlan}
              selectedPlan={selectedPlan}
              title="Trades recommandés"
            />
          )}
          {replayLoading ? (
            <Card>
              <CardContent className="flex items-center justify-center gap-2 py-8 text-sm text-muted">
                <Loader2 className="w-5 h-5 animate-spin text-primary-400" />
                Chargement des trades réalisés…
              </CardContent>
            </Card>
          ) : (
            <RealizedTradesTable
              trades={replayData?.trades || []}
              strategy="smart_money"
              onSelectTrade={handleSelectRealized}
              selectedTrade={
                selectedPlan?.setup
                  ? (replayData?.trades || []).find((t: any) =>
                    t.entry === selectedPlan.entry
                    && t.stop === selectedPlan.stop
                    && (t.setup || 'realized') === selectedPlan.setup
                  ) || null
                  : null
              }
              footnote={
                <>
                  Recommandés = plans SMC analytiques. Réalisés = backtest{' '}
                  <code className="font-mono">smart_money</code> (même moteur que Smart Replay).
                  La stratégie smart_money est calibrée sur TF élevés (4h/1d) : sur 15m–1h
                  et sur actions, peu ou pas de trades est normal (historique Yahoo ~88 bougies
                  en intraday EU).
                </>
              }
            />
          )}
        </>
      )}

      {/* Meta panel — bandeau sous les plans (ex-colonne latérale) */}
      {!isLoading && !isError && data && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Sparkles className="w-3.5 h-3.5 text-primary-400" />
                Signal
              </CardTitle>
            </CardHeader>
            <CardContent>
              {signal && signal.side && signal.side !== 'none' ? (
                <div className="space-y-2 text-sm">
                  <div className="flex items-center gap-2">
                    {signal.side === 'long' && <ArrowUp className="w-4 h-4 text-emerald-400" />}
                    {signal.side === 'short' && <ArrowDown className="w-4 h-4 text-red-400" />}
                    <Badge variant={signal.side === 'long' ? 'success' : 'danger'}>
                      {String(signal.side).toUpperCase()}
                    </Badge>
                    {signal.score != null && (
                      <span className="text-xs text-muted">Score {Number(signal.score).toFixed(2)}</span>
                    )}
                  </div>
                  {signal.setup && (
                    <div className="text-xs text-cyan-400 font-mono">Setup: {signal.setup}</div>
                  )}
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <div className="text-dim flex items-center gap-1"><Target className="w-3 h-3" />Entry</div>
                      <div className="font-mono text-emerald-400">{formatPrice(signal.entry)}</div>
                    </div>
                    <div>
                      <div className="text-dim flex items-center gap-1"><Shield className="w-3 h-3" />Stop</div>
                      <div className="font-mono text-red-400">{formatPrice(signal.stop)}</div>
                    </div>
                    <div>
                      <div className="text-dim flex items-center gap-1"><ArrowUp className="w-3 h-3" />TP</div>
                      <div className="font-mono text-emerald-400">{formatPrice(signal.tp)}</div>
                    </div>
                  </div>
                  {signal.reason && (
                    <div className="text-xs text-muted bg-card-hover p-2 rounded border border-border">
                      {signal.reason}
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-xs text-muted">
                  {signal?.reason || 'Pas de signal actif'}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Bias</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              {bias != null && (
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted">TF courant</span>
                  <Badge variant={bias.trend === 1 ? 'success' : bias.trend === -1 ? 'danger' : 'default'}>
                    {String(bias.label || 'neutre').toUpperCase()}
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
              {session && (
                <div className="flex items-center justify-between text-xs pt-1 border-t border-border">
                  <span className="text-muted">Session</span>
                  <span className="capitalize">{session.name}{session.in_killzone ? ' · KZ' : ''}</span>
                </div>
              )}
            </CardContent>
          </Card>

          {pd && (
            <Card>
              <CardHeader><CardTitle className="text-sm">Premium / Discount</CardTitle></CardHeader>
              <CardContent className="space-y-1 text-xs font-mono">
                <div className="flex justify-between">
                  <span className="text-red-400">Premium (high)</span>
                  <span>{formatPrice(pd.high)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Equilibrium</span>
                  <span>{formatPrice(pd.equilibrium)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-emerald-400">Discount (low)</span>
                  <span>{formatPrice(pd.low)}</span>
                </div>
                {(pd.ote_low != null || pd.ote_high != null) && (
                  <div className="flex justify-between text-dim">
                    <span>OTE</span>
                    <span>{formatPrice(pd.ote_low)} – {formatPrice(pd.ote_high)}</span>
                  </div>
                )}
                <div className="pt-2 border-t border-border">
                  <Badge variant={pd.zone === 'premium' ? 'danger' : pd.zone === 'discount' ? 'success' : 'default'}>
                    Zone: {(pd.zone || '—').toUpperCase()}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          )}

          {cycle && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Recycle className="w-3.5 h-3.5 text-amber-400" />
                  Cycle
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-xs font-mono">
                <div className="flex justify-between">
                  <span className="text-muted">Phase</span>
                  <span className="text-amber-400 capitalize">{cycle.phase || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Progress</span>
                  <span>{Number.isFinite(Number(cycle.progress)) ? `${(Number(cycle.progress) * 100).toFixed(0)}%` : '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Boundary</span>
                  <span className="capitalize">
                    {typeof cycle.boundary === 'string'
                      ? cycle.boundary
                      : formatPrice(cycle.boundary)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-amber-400">Target</span>
                  <span className="text-amber-400">{formatPrice(cycle.target)}</span>
                </div>
              </CardContent>
            </Card>
          )}

          {vp && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <BarChart3 className="w-3.5 h-3.5 text-cyan-400" />
                  Volume Profile
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                <div className="flex justify-between font-mono">
                  <span className="text-cyan-400">POC</span>
                  <span>{formatPrice(vp.poc)}</span>
                </div>
                {vp.hvns?.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {vp.hvns.slice(0, 4).map((h: number, i: number) => (
                      <span key={i} className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400">
                        {formatPrice(h, 0)}
                      </span>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {channel && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Spline className="w-3.5 h-3.5 text-purple-400" />
                  Channel
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-xs font-mono">
                <div className="flex justify-between">
                  <span className="text-muted">Mid start</span>
                  <span>{formatPrice(channel.mid_start)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Mid end</span>
                  <span>{formatPrice(channel.mid_end)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Half width</span>
                  <span>{formatPrice(channel.half_width)}</span>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {!isLoading && !isError && data && <SmartGraphTables data={data} />}
    </div>
  );
}
