/**
 * RPL-001 + RPL-002 — Chart candlestick replay avec overlay markers entrée/sortie.
 *
 * Inspired by `smart-replay-view.tsx` chart setup (createChart + useRef +
 * setData(slice) + setMarkers), adapté pour les trades de backtest.
 */

'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  ColorType,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  type Time,
  type SeriesMarker,
} from 'lightweight-charts';
import { setupAbbr, exitReasonBadge } from '@/lib/exit-reason-badges';
import type { BacktestTrade } from '@/types';
import type { CandleRow } from '@/hooks/use-replay-engine';

interface Props {
  candles: CandleRow[];
  position: number;
  /** Trades d'UNE stratégie (filtrés par le parent). */
  trades: BacktestTrade[];
  onCrosshairMove?: (candle: CandleRow | null) => void;
}

export function ReplayCandlestickChart({ candles, position, trades, onCrosshairMove }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);

  // Create chart once.
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
    const series = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });
    chartRef.current = chart;
    seriesRef.current = series;

    // Crosshair → notifie le parent pour le badge OHLCV.
    if (onCrosshairMove) {
      chart.subscribeCrosshairMove((param) => {
        if (!param || !param.time) {
          onCrosshairMove(null);
          return;
        }
        const candle = candles.find((c) => c.time === (param.time as number));
        onCrosshairMove(candle ?? null);
      });
    }

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
      seriesRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update slice + markers whenever position or data changes.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    if (candles.length === 0) {
      series.setData([]);
      series.setMarkers([]);
      return;
    }

    // Slice candles up to position (inclusive).
    const sliceEnd = Math.max(1, Math.min(position + 1, candles.length));
    const slice = candles.slice(0, sliceEnd).map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    series.setData(slice);

    // Markers : entrées (▲▼) et sorties (●) dont la bar ≤ position.
    const markers: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const entryBar = typeof t.bar === 'number' ? t.bar : null;
      const exitBar = typeof t.exit_bar === 'number' ? t.exit_bar : null;
      const isLong = t.side === 'long';
      const entryColor = isLong ? '#10b981' : '#ef4444';
      const setupTxt = setupAbbr(t.setup, t.side);

      if (entryBar != null && entryBar <= position && candles[entryBar]) {
        markers.push({
          time: candles[entryBar].time as UTCTimestamp,
          position: isLong ? 'belowBar' : 'aboveBar',
          color: entryColor,
          shape: isLong ? 'arrowUp' : 'arrowDown',
          text: setupTxt,
        });
      }
      if (exitBar != null && exitBar <= position && candles[exitBar]) {
        const isWin = (t.pnl ?? 0) >= 0;
        const badge = exitReasonBadge(t.exit_reason);
        markers.push({
          time: candles[exitBar].time as UTCTimestamp,
          position: isLong ? 'aboveBar' : 'belowBar',
          color: isWin ? '#4ade80' : '#f87171',
          shape: 'circle',
          text: `${badge.emoji} ${badge.abbr}`,
        });
      }
    }
    // Tri chronologique obligatoire (LightweightCharts requirement).
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(markers);

    // Scroll vers la dernière barre visible.
    chart.timeScale().scrollToRealTime();
  }, [candles, position, trades]);

  return (
    <div
      ref={containerRef}
      className="w-full h-[min(70vh,720px)] min-h-[400px]"
      style={{ width: '100%' }}
    />
  );
}
