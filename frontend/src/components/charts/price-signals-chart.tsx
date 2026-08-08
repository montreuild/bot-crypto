'use client';

/**
 * BT-001 — Graphique Prix + Signaux avec markers entrée/sortie et stop lines.
 *
 * Composant réutilisable qui :
 *  - affiche les bougies OHLCV (ou une ligne `close`) avec `lightweight-charts` ;
 *  - pose les markers entrée ▲/▼ (`setupAbbr`) et sortie ● (`exitReasonBadge`) ;
 *  - trace le stop initial (amber dashed) et le stop trailing (purple solid)
 *    reconstruits depuis `trade.stop_trail` ;
 *  - expose un toggle line/candles persistant (`localStorage` clé `bt.chartType`) ;
 *  - ouvre un plein écran via `Dialog` Radix avec « Reset zoom » + « Fermer ».
 *
 * Référence Jinja2 : `backtest.html:668-755` (fonction `buildPrice`).
 * Pattern chart/ResizeObserver/cleanup calqué sur `smart-replay-view.tsx`.
 */

import {
  forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from 'react';
import {
  createChart, ColorType, LineStyle, CrosshairMode,
  type IChartApi, type ISeriesApi, type UTCTimestamp, type Time, type SeriesMarker,
} from 'lightweight-charts';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogClose, DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Maximize2, X } from 'lucide-react';
import { exitReasonBadge, setupAbbr } from '@/lib/exit-reason-badges';
// F2 — type CandleRow factorisé dans lib/ohlcv.ts. La fonction cleanOhlcv
// locale reste pour l'instant car elle gère un cas plus large (timeArr mix
// string ISO + epoch secondes, volume optionnel) que la version factorisée.
// Une harmonisation est prévue mais non cassante (cf. audit F2).
import type { CandleRow as SharedCandleRow } from '@/lib/ohlcv';
import type { BacktestTrade, BacktestResult } from '@/types';

// ── Couleurs (BT-001 § Bloc 1 & 5) ──────────────────────────────────────────

const UP_COLOR = '#22c55e';            // bougie up / entrée Long
const DOWN_COLOR = '#ef4444';          // bougie down / entrée Short
const LINE_COLOR = '#4d9fff';          // série ligne (toggle)
const EXIT_WIN_COLOR = '#4ade80';      // sortie ● gain
const EXIT_LOSS_COLOR = '#f87171';     // sortie ● perte
const STOP_INIT_COLOR = 'rgba(251,191,36,.5)';   // amber dashed
const STOP_TRAIL_COLOR = 'rgba(167,139,250,.75)'; // purple solid

const STORAGE_KEY = 'bt.chartType';

// ── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Convertit un `time` (string ISO ou epoch secondes) en `UTCTimestamp`.
 * LightweightCharts attend des secondes depuis l'epoch ; si l'input est une
 * string (ISO ou autre format parsable par `Date`), on passe par `getTime()/1000`.
 */
function toSec(t: string | number | null | undefined): number | null {
  if (t == null) return null;
  if (typeof t === 'number') return Number.isFinite(t) ? t : null;
  const s = String(t).trim();
  if (!s) return null;
  const ms = new Date(s).getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

// F2 : CandleRow étend le type partagé avec un champ `volume?` optionnel
// (la version factorisée dans lib/ohlcv.ts ne l'a pas — harmonisation future).
interface CandleRow extends SharedCandleRow {
  volume?: number;
}

/**
 * Nettoie les arrays parallèles OHLCV en `CandleRow[]` strictement croissants.
 * LightweightCharts exige des `time` uniques et croissants — on force l'unicité
 * en incrémentant de 1 s les doublons (cas rare d'un backend mal aligné).
 */
function cleanOhlcv(
  timeArr: (string | number)[],
  openArr: number[],
  highArr: number[],
  lowArr: number[],
  closeArr: number[],
  volumeArr?: number[],
): CandleRow[] {
  const n = Math.min(
    timeArr.length,
    openArr.length,
    highArr.length,
    lowArr.length,
    closeArr.length,
  );
  const rows: CandleRow[] = [];
  let lastTime = -Infinity;
  for (let i = 0; i < n; i++) {
    const sec = toSec(timeArr[i]);
    if (sec == null) continue;
    let t = sec;
    while (t <= lastTime) t += 1;
    lastTime = t;
    const o = Number(openArr[i]);
    const h = Number(highArr[i]);
    const l = Number(lowArr[i]);
    const c = Number(closeArr[i]);
    if (![o, h, l, c].every(Number.isFinite)) continue;
    rows.push({
      time: t as UTCTimestamp,
      open: o, high: h, low: l, close: c,
      volume: volumeArr ? Number(volumeArr[i]) : undefined,
    });
  }
  return rows;
}

// ── Chart canvas (réutilisé inline + plein écran) ───────────────────────────

interface ChartCanvasProps {
  candles: BacktestResult['ohlcv'] | null | undefined;
  trades: BacktestTrade[];
  chartType: 'candles' | 'line';
  height: number | string;
  /** Message overlay à afficher au-dessus du chart (état vide / pas de trade). */
  emptyMessage?: string | null;
}

export interface ChartCanvasHandle {
  resetZoom: () => void;
}

const ChartCanvas = forwardRef<ChartCanvasHandle, ChartCanvasProps>(function ChartCanvas(
  { candles, trades, chartType, height, emptyMessage },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mainSeriesRef = useRef<ISeriesApi<'Candlestick'> | ISeriesApi<'Line'> | null>(null);
  const stopSeriesRef = useRef<ISeriesApi<'Line'>[]>([]);

  const candleRows = useMemo<CandleRow[]>(() => {
    if (!candles?.time?.length) return [];
    return cleanOhlcv(
      candles.time,
      candles.open ?? [],
      candles.high ?? [],
      candles.low ?? [],
      candles.close ?? [],
      candles.volume,
    );
  }, [candles]);

  // bar index → UTCTimestamp : les trades référencent les bougies par index
  // (`t.bar`, `t.exit_bar`, `t.stop_trail[].bar`), pas par timestamp.
  const timeMap = useMemo<Record<number, UTCTimestamp>>(() => {
    const m: Record<number, UTCTimestamp> = {};
    candleRows.forEach((r, i) => { m[i] = r.time; });
    return m;
  }, [candleRows]);

  useImperativeHandle(ref, () => ({
    resetZoom: () => {
      try { chartRef.current?.timeScale().resetTimeScale(); } catch { /* noop */ }
    },
  }), []);

  // (1) Création du chart — recréé quand `chartType` change (on swap de série).
  // On ne dépend PAS de `candleRows`/`trades` ici : ceux-ci sont poussés via
  // l'effet (2) ci-dessous, sans recréer l'instance (évite le reset du zoom).
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const w = el.clientWidth || 800;
    const h = typeof height === 'number' ? height : el.clientHeight || 300;

    const chart = createChart(el, {
      width: w,
      height: h,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9ca3af',
        fontFamily: 'var(--font-jetbrains), monospace',
      },
      grid: {
        vertLines: { color: 'rgba(31,41,55,.4)' },
        horzLines: { color: 'rgba(31,41,55,.4)' },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#1f2937' },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#1f2937' },
    });
    chartRef.current = chart;

    const mainSeries =
      chartType === 'candles'
        ? chart.addCandlestickSeries({
            upColor: UP_COLOR, downColor: DOWN_COLOR,
            borderUpColor: UP_COLOR, borderDownColor: DOWN_COLOR,
            wickUpColor: UP_COLOR, wickDownColor: DOWN_COLOR,
            priceLineVisible: false, lastValueVisible: false,
          })
        : chart.addLineSeries({
            color: LINE_COLOR, lineWidth: 2,
            priceLineVisible: false, lastValueVisible: false,
          });
    mainSeriesRef.current = mainSeries;

    // ResizeObserver — même pattern que `smart-replay-view.tsx:197-205`.
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height: rh } = entry.contentRect;
        if (width > 0 && rh > 0) {
          chart.applyOptions({ width, height: rh });
        }
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      try { chart.remove(); } catch { /* noop */ }
      chartRef.current = null;
      mainSeriesRef.current = null;
      stopSeriesRef.current = [];
    };
    // `height` est volontairement omis : la taille initiale est calculée une
    // fois au mount, puis le ResizeObserver prend le relais.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartType]);

  // (2) setData + markers + stop lines — rejoué à chaque changement de données.
  useEffect(() => {
    const chart = chartRef.current;
    const series = mainSeriesRef.current;
    if (!chart || !series) return;

    // ── Données bougies / ligne ────────────────────────────────────────────
    if (chartType === 'candles') {
      (series as ISeriesApi<'Candlestick'>).setData(
        candleRows.map((r) => ({
          time: r.time,
          open: r.open, high: r.high, low: r.low, close: r.close,
        })),
      );
    } else {
      (series as ISeriesApi<'Line'>).setData(
        candleRows.map((r) => ({ time: r.time, value: r.close })),
      );
    }

    // ── Markers entrée + sortie ────────────────────────────────────────────
    // Fusionnés puis triés chronologiquement : LightweightCharts EXIGE l'ordre
    // croissant pour `setMarkers`, sinon il lève une erreur au runtime.
    const markers: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const bi = t.bar != null ? Number(t.bar) : NaN;
      const ei = t.exit_bar != null ? Number(t.exit_bar) : NaN;
      const isLong = t.side === 'long';

      // Entrée ▲ (Long) / ▼ (Short) — skip si `bar` absent/hors plage.
      if (Number.isFinite(bi) && bi >= 0 && timeMap[bi]) {
        markers.push({
          time: timeMap[bi],
          position: isLong ? 'belowBar' : 'aboveBar',
          color: isLong ? UP_COLOR : DOWN_COLOR,
          shape: isLong ? 'arrowUp' : 'arrowDown',
          text: setupAbbr(t.setup, t.side),
        });
      }

      // Sortie ● — position inverse de l'entrée ; couleur selon PnL.
      if (Number.isFinite(ei) && ei >= 0 && timeMap[ei]) {
        const badge = exitReasonBadge(t.exit_reason);
        const isWin = (t.pnl ?? 0) >= 0;
        const text = badge.emoji ? `${badge.emoji} ${badge.abbr}` : badge.abbr;
        markers.push({
          time: timeMap[ei],
          position: isLong ? 'aboveBar' : 'belowBar',
          color: isWin ? EXIT_WIN_COLOR : EXIT_LOSS_COLOR,
          shape: 'circle',
          text,
        });
      }
    }
    markers.sort((a, b) => Number(a.time) - Number(b.time));
    try { series.setMarkers(markers); } catch { /* noop */ }

    // ── Stop lines — nettoyage des séries précédentes ──────────────────────
    for (const s of stopSeriesRef.current) {
      try { chart.removeSeries(s); } catch { /* noop */ }
    }
    stopSeriesRef.current = [];

    for (const t of trades) {
      const bi = t.bar != null ? Number(t.bar) : NaN;
      const ei = t.exit_bar != null ? Number(t.exit_bar) : NaN;
      if (!Number.isFinite(bi) || !Number.isFinite(ei)) continue;
      const t0 = timeMap[bi];
      const t1 = timeMap[ei];
      if (!t0 || !t1 || Number(t0) >= Number(t1)) continue;

      // Stop trail filtré sur [bi, ei] (points hors plage ignorés).
      const trail = Array.isArray(t.stop_trail) ? t.stop_trail : [];
      const trailInRange = trail
        .map((p) => ({ bar: Number(p?.bar), stop: Number(p?.stop) }))
        .filter(
          (p) => Number.isFinite(p.bar) && Number.isFinite(p.stop)
            && p.bar >= bi && p.bar <= ei && !!timeMap[p.bar],
        );

      // ── Stop initial (amber dashed) ──────────────────────────────────────
      // Étendue : entrée → premier update du trailing (ou sortie si pas de
      // trailing). Valeur : `stop_initial ?? stop` (alias backend).
      const initialStop = t.stop_initial ?? t.stop;
      const firstTrailTime = trailInRange.length > 0
        ? timeMap[trailInRange[0].bar]
        : t1;
      if (
        initialStop != null && Number.isFinite(initialStop)
        && firstTrailTime && Number(firstTrailTime) > Number(t0)
      ) {
        try {
          const stopS = chart.addLineSeries({
            color: STOP_INIT_COLOR,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          stopS.setData([
            { time: t0, value: initialStop },
            { time: firstTrailTime, value: initialStop },
          ]);
          stopSeriesRef.current.push(stopS);
        } catch { /* noop */ }
      }

      // ── Stop trailing (purple solid) ─────────────────────────────────────
      // ≥ 2 points in-range requis pour tracer une ligne. Le dernier point est
      // étendu jusqu'à la barre de sortie (sinon la ligne s'arrête net au
      // dernier update du trail).
      if (trailInRange.length >= 2) {
        try {
          const trailData = trailInRange.map((p) => ({
            time: timeMap[p.bar],
            value: p.stop,
          }));
          const lastPt = trailData[trailData.length - 1];
          if (Number(lastPt.time) < Number(t1)) {
            trailData.push({ time: t1, value: lastPt.value });
          }
          const trailS = chart.addLineSeries({
            color: STOP_TRAIL_COLOR,
            lineWidth: 2,
            lineStyle: LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          trailS.setData(trailData);
          stopSeriesRef.current.push(trailS);
        } catch { /* noop */ }
      }
    }

    try { chart.timeScale().fitContent(); } catch { /* noop */ }
  }, [candleRows, trades, timeMap, chartType]);

  return (
    <div className="relative w-full" style={{ height }}>
      <div ref={containerRef} className="absolute inset-0" />
      {emptyMessage && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="rounded-md border border-border bg-background/70 px-3 py-1.5 text-xs text-muted">
            {emptyMessage}
          </div>
        </div>
      )}
    </div>
  );
});

// ── Composant principal ─────────────────────────────────────────────────────

export interface PriceSignalsChartProps {
  candles: BacktestResult['ohlcv'];
  trades: BacktestTrade[];
  chartType?: 'candles' | 'line';
  height?: number;
  onChartTypeChange?: (t: 'candles' | 'line') => void;
}

export function PriceSignalsChart({
  candles,
  trades,
  chartType,
  height = 300,
  onChartTypeChange,
}: PriceSignalsChartProps) {
  // État local persistant — `localStorage` clé `bt.chartType` sert de défaut
  // initial. Lu dans un effet (pas dans l'initialiseur de useState) pour
  // éviter une mismatch d'hydratation SSR (le serveur n'a pas localStorage).
  const [internalType, setInternalType] = useState<'candles' | 'line'>(chartType ?? 'candles');
  const [isFullscreen, setIsFullscreen] = useState(false);

  const inlineRef = useRef<ChartCanvasHandle>(null);
  const fsRef = useRef<ChartCanvasHandle>(null);

  useEffect(() => {
    // Si le parent contrôle via `chartType`, on ne lit pas localStorage.
    if (chartType) return;
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === 'candles' || saved === 'line') {
        setInternalType((cur) => (cur === saved ? cur : saved));
      }
    } catch { /* noop */ }
  }, [chartType]);

  // Si le parent passe `chartType`, on respecte sa valeur (mode contrôlé).
  // Sinon, on utilise l'état interne persistant.
  const activeType = chartType ?? internalType;

  const setType = (t: 'candles' | 'line') => {
    setInternalType(t);
    if (typeof window !== 'undefined') {
      try { window.localStorage.setItem(STORAGE_KEY, t); } catch { /* noop */ }
    }
    onChartTypeChange?.(t);
  };

  const hasCandles = !!(candles?.time?.length && (candles.close?.length ?? 0) > 0);
  const emptyMessage = !hasCandles
    ? 'Aucune donnée OHLCV'
    : trades.length === 0
      ? 'Aucun trade pour cette stratégie'
      : null;

  return (
    <div className="w-full">
      {/* Toolbar : toggle line/candles · Reset zoom · Plein écran */}
      <div className="mb-2 flex items-center justify-end gap-2">
        <div className="flex overflow-hidden rounded-md border border-border">
          <Button
            variant={activeType === 'candles' ? 'primary' : 'ghost'}
            size="sm"
            className="rounded-none border-0"
            onClick={() => setType('candles')}
          >
            Chandelier
          </Button>
          <Button
            variant={activeType === 'line' ? 'primary' : 'ghost'}
            size="sm"
            className="rounded-none border-0"
            onClick={() => setType('line')}
          >
            Ligne
          </Button>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => inlineRef.current?.resetZoom()}
        >
          Reset zoom
        </Button>
        <Button
          variant="outline"
          size="icon"
          onClick={() => setIsFullscreen(true)}
          aria-label="Plein écran"
        >
          <Maximize2 className="h-4 w-4" />
        </Button>
      </div>

      {/* Chart inline */}
      <ChartCanvas
        ref={inlineRef}
        candles={candles}
        trades={trades}
        chartType={activeType}
        height={height}
        emptyMessage={emptyMessage}
      />

      {/* Légende (BT-001 § Bloc 2) */}
      <div className="mt-2 space-y-1 text-[10px]">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span className="text-emerald-500">▲ Entrée Long</span>
          <span className="text-red-500">▼ Entrée Short</span>
          <span className="text-emerald-400">● Sortie gain</span>
          <span className="text-red-400">● Sortie perte</span>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span className="text-amber-400">┄┄ Stop initial</span>
          <span className="text-purple-400">── Stop trailing</span>
        </div>
      </div>

      {/* Plein écran (BT-001 § Bloc 1) */}
      <Dialog open={isFullscreen} onOpenChange={setIsFullscreen}>
        <DialogContent
          className="flex h-[calc(100vh-100px)] w-[95vw] max-w-none flex-col gap-3 p-4"
          showClose={false}
        >
          <DialogHeader className="flex flex-row items-center justify-between space-y-0">
            <DialogTitle>Prix &amp; signaux</DialogTitle>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => fsRef.current?.resetZoom()}
              >
                Reset zoom
              </Button>
              <DialogClose asChild>
                <Button variant="outline" size="sm">
                  <X className="h-3.5 w-3.5" /> Fermer
                </Button>
              </DialogClose>
            </div>
          </DialogHeader>
          <DialogDescription className="sr-only">
            Graphique des prix et signaux en plein écran.
          </DialogDescription>
          <div className="min-h-0 flex-1">
            <ChartCanvas
              ref={fsRef}
              candles={candles}
              trades={trades}
              chartType={activeType}
              height="100%"
              emptyMessage={emptyMessage}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
