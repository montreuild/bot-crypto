'use client';

/**
 * Onglet Scanner de `/market` :
 *  - table multi-symboles triable + filtres (régime, ADX, ATR, RSI)
 *  - Fast Analyse + prédictions par stratégie
 *  - Signaux SMC + Top opportunités (2 colonnes) et Marché en dessous
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import {
  Search, Loader2, RefreshCw, ArrowUpDown,
} from 'lucide-react';
import { PredictionsPanel } from '@/components/cards/predictions-panel';
import { OpportunitiesWidget } from '@/components/cards/opportunities-widget';
import { FastAnalysisPanel } from '@/components/cards/fast-analysis-panel';
import { useSignals } from '@/hooks/use-api';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { cn, errorMessage } from '@/lib/utils';

interface ScannerViewProps {
  initialSymbol?: string;
  initialTf?: string;
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

export function ScannerView({ initialSymbol, initialTf }: ScannerViewProps) {
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

  const signalsQuery = useSignals(scanned?.symbol ?? '', scanned?.tf ?? '', 300, !!scanned);

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
    } catch (e) {
      toast.error(`Scan : ${errorMessage(e)}`);
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
    } catch (e) {
      toast.error(errorMessage(e));
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

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Scanner</h2>
          <p className="text-sm text-muted mt-1">
            Multi-symboles · Fast Analyse
          </p>
        </div>
      </div>

      {/* Signaux SMC + Top opportunités — 2 colonnes */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <OpportunitiesWidget timeframe={tf} limit={12} />
      </div>

      {faResult && (
        <FastAnalysisPanel result={faResult} symbol={selected} timeframe={tf} />
      )}

      {/* Marché : filtres + scan + tableau, pleine largeur */}
      <div className="grid grid-cols-1 gap-4">
        <Card>
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
            <div className="flex items-center gap-2">
              {loading && <Loader2 className="w-4 h-4 animate-spin text-primary-400" />}
              <Button size="sm" variant="ghost" onClick={() => runFast(selected)} disabled={faLoading}>
                {faLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
                Fast Analyse ({selected})
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-3 p-3 pt-0">
            {/* Formulaire TF + filtres + scan — dans le volet Marché */}
            <div className="flex flex-col gap-2 pb-2 border-b border-border">
              <div className="flex flex-wrap items-end gap-2">
                <div>
                  <label className="text-[10px] text-dim block mb-1">Timeframe</label>
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
              <Button variant="outline" size="sm" onClick={runScreen} disabled={loading} className="w-fit">
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                Scanner le marché
              </Button>
            </div>
            <div className="overflow-auto max-h-[480px] -mx-1">
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
      </div>

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
