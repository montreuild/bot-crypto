'use client';

/**
 * Onglet Scanner de `/market` — parité Jinja2 scanner.html :
 *  - table multi-symboles triable + filtres (régime, ADX, ATR, RSI)
 *  - chart multi-panneaux (prix + EMA/BB, volume, RSI, MACD)
 *  - Fast Analyse + prédictions par stratégie
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import {
  Search, Loader2, ArrowRight, RefreshCw, ArrowUpDown,
} from 'lucide-react';
import { PredictionsPanel } from '@/components/cards/predictions-panel';
import { OpportunitiesWidget } from '@/components/cards/opportunities-widget';
import { useSignals } from '@/hooks/use-api';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { cn, formatUSD } from '@/lib/utils';
import {
  createChart, ColorType, LineStyle, CrosshairMode,
  type IChartApi, type ISeriesApi, type UTCTimestamp,
} from 'lightweight-charts';

interface ScannerViewProps {
  initialSymbol?: string;
  initialTf?: string;
  onAnalyze?: (symbol: string, tf: string) => void;
}

type SortKey = 'symbol' | 'adx' | 'atr_pct' | 'rsi' | 'volume_24h' | 'regime';

interface ScreenRow {
  symbol: string;
  indicators: Record<string, any>;
  volume_24h: number;
  regime: string;
  regime_label?: string;
  strategies?: string[];
  bars?: number;
}

const FILTERS_KEY = 'scanner_filters_v1';

function loadFilters() {
  try {
    const raw = localStorage.getItem(FILTERS_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return null;
}

export function ScannerView({ initialSymbol, initialTf, onAnalyze }: ScannerViewProps) {
  const saved = typeof window !== 'undefined' ? loadFilters() : null;
  const { defaultTf } = useTradingTimeframes(initialTf || saved?.tf || '1h');
  const [tf, setTf] = useState(initialTf || saved?.tf || '1h');
  const [limit, setLimit] = useState(200);
  const [rows, setRows] = useState<ScreenRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(initialSymbol || 'BTC/USDC');
  const [sortKey, setSortKey] = useState<SortKey>('volume_24h');
  const [sortAsc, setSortAsc] = useState(false);

  // Filtres
  const [fRegime, setFRegime] = useState<string>(saved?.fRegime || '');
  const [fAdxMin, setFAdxMin] = useState(saved?.fAdxMin ?? 0);
  const [fAtrMin, setFAtrMin] = useState(saved?.fAtrMin ?? 0);
  const [fAtrMax, setFAtrMax] = useState(saved?.fAtrMax ?? 100);
  const [fRsiLo, setFRsiLo] = useState(saved?.fRsiLo ?? 0);
  const [fRsiHi, setFRsiHi] = useState(saved?.fRsiHi ?? 100);

  // Fast analyse + prédictions
  const [faResult, setFaResult] = useState<any>(null);
  const [faLoading, setFaLoading] = useState(false);
  const [scanned, setScanned] = useState<{ symbol: string; tf: string } | null>(null);

  // Chart toggles
  const [showEma, setShowEma] = useState(true);
  const [showBb, setShowBb] = useState(true);

  const signalsQuery = useSignals(scanned?.symbol ?? '', scanned?.tf ?? '', 300, !!scanned);

  // Chart refs (tous les hooks avant les effects)
  const priceRef = useRef<HTMLDivElement>(null);
  const volRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);
  const chartsRef = useRef<{
    price?: IChartApi; vol?: IChartApi; rsi?: IChartApi; macd?: IChartApi;
    candle?: ISeriesApi<'Candlestick'>;
  }>({});

  useEffect(() => {
    if (!initialTf && defaultTf) {
      setTf((t: string) => (t === '1h' || !t ? defaultTf : t));
    }
  }, [defaultTf, initialTf]);

  const persistFilters = useCallback(() => {
    try {
      localStorage.setItem(FILTERS_KEY, JSON.stringify({
        tf, fRegime, fAdxMin, fAtrMin, fAtrMax, fRsiLo, fRsiHi,
      }));
    } catch { /* ignore */ }
  }, [tf, fRegime, fAdxMin, fAtrMin, fAtrMax, fRsiLo, fRsiHi]);

  const runScreen = async () => {
    setLoading(true);
    try {
      const res = await api.scanMarket(tf, limit);
      setRows(res?.results || []);
      toast.success(`${(res?.results || []).length} symboles scannés`);
      persistFilters();
    } catch (e: any) {
      toast.error(`Scan : ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const runFast = async (sym = selected) => {
    setFaLoading(true);
    try {
      const r = await api.fastAnalysis(sym, tf);
      setFaResult(r);
      setScanned({ symbol: sym, tf });
      setSelected(sym);
      toast.success(`Fast Analyse ${sym} ${tf}`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setFaLoading(false);
    }
  };

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      const ind = r.indicators || {};
      const adx = Number(ind.adx ?? 0);
      const atr = Number(ind.atr_pct ?? 0);
      const rsi = Number(ind.rsi ?? 50);
      if (fRegime && String(r.regime_label || r.regime || '').toLowerCase() !== fRegime.toLowerCase()) return false;
      if (adx < fAdxMin) return false;
      if (atr < fAtrMin || atr > fAtrMax) return false;
      if (rsi < fRsiLo || rsi > fRsiHi) return false;
      return true;
    });
  }, [rows, fRegime, fAdxMin, fAtrMin, fAtrMax, fRsiLo, fRsiHi]);

  const sorted = useMemo(() => {
    const list = [...filtered];
    const dir = sortAsc ? 1 : -1;
    list.sort((a, b) => {
      const av = sortKey === 'symbol' ? a.symbol
        : sortKey === 'volume_24h' ? a.volume_24h
        : sortKey === 'regime' ? String(a.regime_label || a.regime || '')
        : Number(a.indicators?.[sortKey] ?? 0);
      const bv = sortKey === 'symbol' ? b.symbol
        : sortKey === 'volume_24h' ? b.volume_24h
        : sortKey === 'regime' ? String(b.regime_label || b.regime || '')
        : Number(b.indicators?.[sortKey] ?? 0);
      if (typeof av === 'string' && typeof bv === 'string') return av.localeCompare(bv) * dir;
      return ((av as number) - (bv as number)) * dir;
    });
    return list;
  }, [filtered, sortKey, sortAsc]);

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setSortAsc((v) => !v);
    else { setSortKey(k); setSortAsc(k === 'symbol' || k === 'regime'); }
  };

  // ── Multi-panel chart ──────────────────────────────────────────────────
  useEffect(() => {
    if (!priceRef.current || !volRef.current || !rsiRef.current || !macdRef.current) return;
    const common = {
      layout: {
        background: { type: ColorType.Solid, color: '#0f1419' },
        textColor: '#9ca3af',
        fontFamily: 'var(--font-jetbrains), monospace',
      },
      grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#1f2937' },
      timeScale: { borderColor: '#1f2937', timeVisible: true, secondsVisible: false },
      autoSize: true,
    };
    const price = createChart(priceRef.current, { ...common });
    const candle = price.addCandlestickSeries({
      upColor: '#10b981', downColor: '#ef4444',
      borderUpColor: '#10b981', borderDownColor: '#ef4444',
      wickUpColor: '#10b981', wickDownColor: '#ef4444',
    });
    // timeScale visible=false mais plage TEMPORELLE partagée (pas logical :
    // le RSI a moins de points → désync si on utilise logical range).
    const subTs = { visible: false, borderColor: '#1f2937', timeVisible: true, secondsVisible: false };
    const vol = createChart(volRef.current, { ...common, timeScale: subTs });
    const rsi = createChart(rsiRef.current, { ...common, timeScale: subTs });
    const macd = createChart(macdRef.current, { ...common, timeScale: subTs });

    // Sync par plage de temps (évite RSI tronqué vs prix)
    let syncing = false;
    const syncFrom = (source: IChartApi) => {
      if (syncing) return;
      const range = source.timeScale().getVisibleRange();
      if (!range) return;
      syncing = true;
      try {
        for (const ch of [price, vol, rsi, macd]) {
          if (ch !== source) {
            try { ch.timeScale().setVisibleRange(range); } catch { /* hors domaine */ }
          }
        }
      } finally {
        syncing = false;
      }
    };
    price.timeScale().subscribeVisibleTimeRangeChange(() => syncFrom(price));
    vol.timeScale().subscribeVisibleTimeRangeChange(() => syncFrom(vol));
    rsi.timeScale().subscribeVisibleTimeRangeChange(() => syncFrom(rsi));
    macd.timeScale().subscribeVisibleTimeRangeChange(() => syncFrom(macd));

    chartsRef.current = { price, vol, rsi, macd, candle };
    return () => {
      price.remove(); vol.remove(); rsi.remove(); macd.remove();
      chartsRef.current = {};
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const c = chartsRef.current;
      if (!c.price || !c.candle || !c.vol || !c.rsi || !c.macd) return;
      try {
        const payload = await api.getScannerChart(selected, tf, 300);
        if (cancelled) return;
        const candles = (payload.candles || []).map((x: any) => ({
          time: x.time as UTCTimestamp,
          open: x.open, high: x.high, low: x.low, close: x.close,
        }));
        c.candle!.setData(candles);

        // Clear old line series by recreating charts is heavy — remove via store of series refs
        // Instead: remove all line series if we tracked them. Simpler: re-set data only on candle,
        // and re-add indicators by disposing chart lines through a tag.
        // Track indicator series on chartsRef as array
        const prev = (chartsRef.current as any)._ind as ISeriesApi<any>[] | undefined;
        if (prev) {
          for (const s of prev) {
            try { c.price?.removeSeries(s); } catch { /* */ }
            try { c.vol?.removeSeries(s); } catch { /* */ }
            try { c.rsi?.removeSeries(s); } catch { /* */ }
            try { c.macd?.removeSeries(s); } catch { /* */ }
          }
        }
        const indSeries: ISeriesApi<any>[] = [];
        const ind = payload.indicators || {};

        const addLine = (chart: IChartApi, data: any[], color: string, width: 1 | 2 = 1) => {
          if (!data?.length) return;
          const s = chart.addLineSeries({
            color, lineWidth: width,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          s.setData(data.map((p: any) => ({ time: p.time as UTCTimestamp, value: p.value })));
          indSeries.push(s);
        };

        if (showEma) {
          addLine(c.price!, ind.ema20 || [], '#22d3ee');
          addLine(c.price!, ind.ema50 || [], '#a78bfa');
          addLine(c.price!, ind.ema200 || [], '#f59e0b', 2);
        }
        if (showBb) {
          addLine(c.price!, ind.bb_upper || [], 'rgba(148,163,184,.7)');
          addLine(c.price!, ind.bb_mid || [], 'rgba(148,163,184,.4)');
          addLine(c.price!, ind.bb_lower || [], 'rgba(148,163,184,.7)');
        }

        // Volume histogram
        const volS = c.vol!.addHistogramSeries({ priceFormat: { type: 'volume' }, priceLineVisible: false });
        volS.setData((ind.volume || []).map((p: any) => ({
          time: p.time as UTCTimestamp,
          value: p.value,
          color: p.color || 'rgba(52,211,153,.4)',
        })));
        indSeries.push(volS);

        // RSI
        addLine(c.rsi!, ind.rsi || [], '#e879f9', 2);
        // levels 30/70 as price lines on a dummy series
        const rsiLine = c.rsi!.addLineSeries({ color: 'transparent', lastValueVisible: false, priceLineVisible: false });
        if ((ind.rsi || []).length) {
          rsiLine.setData((ind.rsi || []).map((p: any) => ({ time: p.time as UTCTimestamp, value: p.value })));
          rsiLine.createPriceLine({ price: 70, color: 'rgba(239,68,68,.5)', lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
          rsiLine.createPriceLine({ price: 30, color: 'rgba(16,185,129,.5)', lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
        }
        indSeries.push(rsiLine);

        // MACD
        addLine(c.macd!, ind.macd || [], '#22d3ee', 2);
        addLine(c.macd!, ind.macd_signal || [], '#f59e0b');
        const hist = c.macd!.addHistogramSeries({ priceLineVisible: false });
        hist.setData((ind.macd_hist || []).map((p: any) => ({
          time: p.time as UTCTimestamp,
          value: p.value,
          color: p.value >= 0 ? 'rgba(16,185,129,.5)' : 'rgba(239,68,68,.5)',
        })));
        indSeries.push(hist);

        (chartsRef.current as any)._ind = indSeries;
        c.price!.timeScale().fitContent();
      } catch (e: any) {
        if (!cancelled) toast.error(`Chart: ${e.message}`);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [selected, tf, showEma, showBb]);

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Scanner</h2>
          <p className="text-sm text-muted mt-1">
            Multi-symboles · chart 4 panneaux · Fast Analyse
          </p>
        </div>
        {onAnalyze && (
          <Button variant="ghost" size="sm" onClick={() => onAnalyze(selected, tf)}>
            Labo {selected}
            <ArrowRight className="w-3.5 h-3.5 ml-1" />
          </Button>
        )}
      </div>

      {/* Formulaire TF + filtres + scan — entre titre et tableau Marché */}
      <Card>
        <CardContent className="flex flex-col gap-3 py-3">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="text-[10px] text-dim block mb-1">TF</label>
              <TimeframeButtons value={tf} onChange={setTf} size="sm" />
            </div>
            <div>
              <label className="text-[10px] text-dim block mb-1">Régime</label>
              <select aria-label="Régime" value={fRegime} onChange={(e) => setFRegime(e.target.value)}
                className="px-2 py-1.5 bg-card-hover border border-border rounded-md text-xs">
                <option value="">Tous</option>
                <option value="trend">Trend</option>
                <option value="range">Range</option>
                <option value="volatile">Volatile</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-dim block mb-1">ADX ≥</label>
              <input aria-label="ADX min" type="number" value={fAdxMin} onChange={(e) => setFAdxMin(Number(e.target.value))}
                className="w-16 px-2 py-1.5 bg-card-hover border border-border rounded-md text-xs font-mono" />
            </div>
            <div>
              <label className="text-[10px] text-dim block mb-1">ATR% min–max</label>
              <div className="flex gap-1">
                <input aria-label="ATR min" type="number" value={fAtrMin} onChange={(e) => setFAtrMin(Number(e.target.value))}
                  className="w-14 px-2 py-1.5 bg-card-hover border border-border rounded-md text-xs font-mono" />
                <input aria-label="ATR max" type="number" value={fAtrMax} onChange={(e) => setFAtrMax(Number(e.target.value))}
                  className="w-14 px-2 py-1.5 bg-card-hover border border-border rounded-md text-xs font-mono" />
              </div>
            </div>
            <div>
              <label className="text-[10px] text-dim block mb-1">RSI lo–hi</label>
              <div className="flex gap-1">
                <input aria-label="RSI lo" type="number" value={fRsiLo} onChange={(e) => setFRsiLo(Number(e.target.value))}
                  className="w-14 px-2 py-1.5 bg-card-hover border border-border rounded-md text-xs font-mono" />
                <input aria-label="RSI hi" type="number" value={fRsiHi} onChange={(e) => setFRsiHi(Number(e.target.value))}
                  className="w-14 px-2 py-1.5 bg-card-hover border border-border rounded-md text-xs font-mono" />
              </div>
            </div>
            <Button size="sm" variant="ghost" onClick={() => {
              setFRegime(''); setFAdxMin(0); setFAtrMin(0); setFAtrMax(100); setFRsiLo(0); setFRsiHi(100);
            }}>✕ Reset</Button>
          </div>
          <div>
            <Button variant="outline" size="sm" onClick={runScreen} disabled={loading}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              Scanner le marché
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">
        {/* Table multi-symboles */}
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle className="text-sm">
              Marché
              {rows.length > 0 && (
                <span className="text-dim font-normal ml-2 text-xs">
                  {sorted.length} affiché{sorted.length > 1 ? 's' : ''}
                  {sorted.length !== rows.length ? ` / ${rows.length}` : ''}
                </span>
              )}
            </CardTitle>
            {loading && <Loader2 className="w-4 h-4 animate-spin text-primary-400" />}
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-auto max-h-[520px]">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-card z-10">
                  <tr className="text-left text-dim border-b border-border">
                    {([
                      ['symbol', 'Symbole'],
                      ['regime', 'Régime'],
                      ['adx', 'ADX'],
                      ['atr_pct', 'ATR%'],
                      ['rsi', 'RSI'],
                      ['volume_24h', 'Vol 24h'],
                    ] as [SortKey, string][]).map(([k, lab]) => (
                      <th key={k} className="p-2 font-medium">
                        <button type="button" className="inline-flex items-center gap-0.5 hover:text-foreground"
                          onClick={() => toggleSort(k)}>
                          {lab}
                          <ArrowUpDown className={cn('w-3 h-3', sortKey === k ? 'text-primary-400' : 'opacity-30')} />
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="p-6 text-center text-muted">
                        {rows.length === 0
                          ? 'Lancez « Scanner le marché » pour peupler la table'
                          : 'Aucun symbole ne passe les filtres'}
                      </td>
                    </tr>
                  ) : sorted.map((r) => {
                    const ind = r.indicators || {};
                    const active = r.symbol === selected;
                    return (
                      <tr
                        key={r.symbol}
                        onClick={() => setSelected(r.symbol)}
                        onDoubleClick={() => runFast(r.symbol)}
                        className={cn(
                          'border-b border-border/30 cursor-pointer hover:bg-card-hover',
                          active && 'bg-primary-500/10',
                        )}
                      >
                        <td className="p-2 font-mono font-semibold">{r.symbol}</td>
                        <td className="p-2">
                          <Badge variant="muted" className="text-[9px]">
                            {r.regime_label || r.regime || '—'}
                          </Badge>
                        </td>
                        <td className="p-2 font-mono text-right">{Number(ind.adx ?? 0).toFixed(1)}</td>
                        <td className="p-2 font-mono text-right">{Number(ind.atr_pct ?? 0).toFixed(2)}</td>
                        <td className="p-2 font-mono text-right">{Number(ind.rsi ?? 0).toFixed(1)}</td>
                        <td className="p-2 font-mono text-right text-dim">
                          {(r.volume_24h / 1e6).toFixed(1)}M
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* Chart 4 panneaux */}
        <Card className="xl:col-span-3 p-0 overflow-hidden">
          <CardHeader className="px-3 py-2 border-b border-border">
            <CardTitle className="text-sm font-mono">{selected} · {tf}</CardTitle>
            <div className="flex items-center gap-3 text-[10px]">
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="checkbox" checked={showEma} onChange={(e) => setShowEma(e.target.checked)} /> EMA
              </label>
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="checkbox" checked={showBb} onChange={(e) => setShowBb(e.target.checked)} /> BB
              </label>
              <Button size="sm" variant="ghost" onClick={() => runFast(selected)} disabled={faLoading}>
                {faLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
                Fast Analyse
              </Button>
            </div>
          </CardHeader>
          <div className="flex flex-col">
            <div className="text-[9px] text-dim px-2 pt-1">Prix</div>
            <div ref={priceRef} className="w-full h-[260px]" />
            <div className="text-[9px] text-dim px-2">Volume</div>
            <div ref={volRef} className="w-full h-[80px]" />
            <div className="text-[9px] text-dim px-2">RSI</div>
            <div ref={rsiRef} className="w-full h-[90px]" />
            <div className="text-[9px] text-dim px-2">MACD</div>
            <div ref={macdRef} className="w-full h-[100px]" />
          </div>
        </Card>
      </div>

      {/* Fast Analyse — grille d'indicateurs IS/OOS (contrat réel de l'API) */}
      {faResult && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
              <Search className="w-4 h-4 text-primary-400" />
              Fast Analyse · {selected} · {tf}
              {faResult.best && (
                <Badge variant="success" className="text-[10px]">
                  Meilleur OOS : {faResult.best}
                </Badge>
              )}
              {faResult.bars != null && (
                <span className="text-[10px] text-dim font-normal ml-auto">
                  {faResult.bars} barres · OOS {faResult.oos_bars ?? '—'}
                </span>
              )}
            </CardTitle>
            <p className="text-[11px] text-muted font-normal">
              Chaque ligne = signal d&apos;indicateur testé en backtest simple (maker/taker, full vs OOS).
            </p>
          </CardHeader>
          <CardContent className="p-0">
            {faResult.error ? (
              <p className="text-xs text-red-400 p-4">{faResult.error}</p>
            ) : !Array.isArray(faResult.rows) || faResult.rows.length === 0 ? (
              <p className="text-xs text-muted p-4 text-center">
                Aucun résultat (historique &lt; 260 barres ou erreur de calcul)
              </p>
            ) : (
              <div className="overflow-x-auto max-h-80">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card z-10">
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2 font-medium">Signal</th>
                      <th className="p-2 font-medium">Famille</th>
                      <th className="p-2 font-medium text-right">OOS n</th>
                      <th className="p-2 font-medium text-right">OOS PnL</th>
                      <th className="p-2 font-medium text-right">OOS PF</th>
                      <th className="p-2 font-medium text-right">OOS WR</th>
                      <th className="p-2 font-medium text-right">Full PnL</th>
                      <th className="p-2 font-medium">Edge</th>
                    </tr>
                  </thead>
                  <tbody>
                    {faResult.rows.slice(0, 24).map((row: any, i: number) => {
                      const oos = row.maker?.oos || {};
                      const full = row.maker?.full || {};
                      const pnl = Number(oos.pnl ?? 0);
                      const isBest = faResult.best && row.signal === faResult.best;
                      return (
                        <tr
                          key={`${row.signal}-${i}`}
                          className={cn(
                            'border-b border-border/30',
                            isBest && 'bg-emerald-500/10',
                          )}
                        >
                          <td className="p-2 font-mono max-w-[14rem] truncate" title={row.signal}>
                            {row.signal}
                          </td>
                          <td className="p-2">
                            <Badge variant="muted" className="text-[9px]">{row.kind || '—'}</Badge>
                          </td>
                          <td className="p-2 text-right font-mono">{oos.n ?? '—'}</td>
                          <td className={cn(
                            'p-2 text-right font-mono font-semibold',
                            pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-muted',
                          )}>
                            {Number.isFinite(pnl) ? pnl.toFixed(1) : '—'}
                          </td>
                          <td className="p-2 text-right font-mono">
                            {oos.pf != null ? Number(oos.pf).toFixed(2) : '—'}
                          </td>
                          <td className="p-2 text-right font-mono">
                            {oos.wr != null ? `${(Number(oos.wr) * 100).toFixed(0)}%` : '—'}
                          </td>
                          <td className="p-2 text-right font-mono text-dim">
                            {full.pnl != null ? Number(full.pnl).toFixed(1) : '—'}
                          </td>
                          <td className="p-2 text-dim text-[10px]">{row.edge || (isBest ? '★' : '—')}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Top opportunités + signaux SMC */}
      <OpportunitiesWidget timeframe={tf} limit={12} />

      {scanned && (signalsQuery.data?.signals?.length > 0 || signalsQuery.isLoading) && (
        <PredictionsPanel
          signals={signalsQuery.data?.signals || []}
          symbol={scanned.symbol}
          timeframe={scanned.tf}
        />
      )}
    </div>
  );
}
