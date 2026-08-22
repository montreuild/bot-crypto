'use client';

/**
 * Onglet Smart Replay de `/market` — rejeu bougie par bougie des calques SMC.
 *
 * ARCH-02 : le graphique vit dans `use-smart-replay-chart`, la lecture dans
 * `use-replay-transport`, le cycle de vie des entités dans
 * `smart-replay-entities`. Il ne reste ici que l'assemblage et le rendu.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertCircle, ChevronLeft, ChevronRight, Droplets, Layers, Loader2,
  Pause, Play, SkipBack, SkipForward, Waves,
} from 'lucide-react';
import { toast } from 'sonner';

import {
  RealizedTradesTable, TradePlansTable, type RealizedTrade, type TradePlan,
} from '@/components/cards/trade-plans-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { SymbolSearchInput } from '@/components/ui/symbol-search';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import {
  lastStructureAt, openTradesAt, closedTradesAt, recentAlive, replayCandles,
} from '@/components/views/smart-replay-entities';
import { useSMC, useSMCReplay } from '@/hooks/use-api';
import { SPEEDS, useReplayTransport } from '@/hooks/use-replay-transport';
import { useSmartReplayChart } from '@/hooks/use-smart-replay-chart';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { cleanOhlcv, type CandleRow } from '@/lib/ohlcv';
import { cn, formatDateTime, formatPct, formatUSD } from '@/lib/utils';

export function SmartReplayView() {
  const { defaultTf } = useTradingTimeframes('4h');
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState<string>(defaultTf || '4h');
  const [selectedPlan, setSelectedPlan] = useState<TradePlan | null>(null);

  const { data, isLoading, isError, error } = useSMCReplay(symbol, timeframe, 1600);
  // Plans SMC (analytiques) — même source que Smart Graph.
  const { data: smcData } = useSMC(symbol, timeframe, 1200);

  useEffect(() => { setSelectedPlan(null); }, [symbol, timeframe]);

  const nBars = data?.n_bars ?? 0;
  const cleanedCandles = useMemo<CandleRow[]>(
    () => replayCandles(data, cleanOhlcv),
    [data?.ohlcv, data?.candles],   // eslint-disable-line react-hooks/exhaustive-deps
  );

  const {
    currentIndex, isPlaying, speedIdx, setSpeedIdx,
    seekTo, goToStart, goToEnd, stepBack, stepForward, jump, togglePlay,
  } = useReplayTransport(nBars);

  const { containerRef } = useSmartReplayChart({
    data, candles: cleanedCandles, currentIndex, selectedPlan,
  });

  const activeOrderBlocks = useMemo(
    () => recentAlive(data?.order_blocks, currentIndex), [data?.order_blocks, currentIndex]);
  const activeLiquidityPools = useMemo(
    () => recentAlive(data?.liquidity_pools, currentIndex), [data?.liquidity_pools, currentIndex]);
  const activeFvgs = useMemo(
    () => recentAlive(data?.fvgs, currentIndex), [data?.fvgs, currentIndex]);
  const lastStructure = useMemo(
    () => lastStructureAt(data?.structure, currentIndex), [data?.structure, currentIndex]);
  const openTrades = useMemo(
    () => openTradesAt(data?.trades, currentIndex), [data?.trades, currentIndex]);
  const closedTrades = useMemo(
    () => closedTradesAt(data?.trades, currentIndex), [data?.trades, currentIndex]);

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
            <SymbolSearchInput value={symbol} onChange={setSymbol} id="smart-replay-symbol" />
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
              onChange={(e) => seekTo(Number(e.target.value))}
              className="w-full accent-primary-400"
              aria-label="Position dans le replay"
            />
          </div>

          <div className="text-xs font-mono text-muted">
            {currentIndex + 1} / {nBars}
          </div>
        </CardContent>
      </Card>

      {/* Aligné Smart Graph : recommandés (SMC) + réalisés (backtest replay) */}
      <TradePlansTable
        plans={(smcData?.trade_plans || []) as TradePlan[]}
        selectedPlan={selectedPlan}
        onSelectPlan={(p) => setSelectedPlan((prev) =>
          prev && prev.entry === p.entry && prev.setup === p.setup ? null : p)}
        title="Trades recommandés"
      />
      <RealizedTradesTable
        trades={(data?.trades || closedTrades) as any}
        strategy="smart_money"
        onSelectTrade={(t: RealizedTrade) => {
          const plan: TradePlan = {
            side: t.side,
            setup: t.setup || 'realized',
            entry: Number(t.entry ?? t.entry_price),
            stop: Number(t.stop),
            tp: Number(t.tp ?? t.take_profit),
            signal_time: t.signal_time ?? null,
            status: 'immediate',
          };
          setSelectedPlan((prev) =>
            prev && prev.entry === plan.entry && prev.setup === plan.setup ? null : plan,
          );
        }}
        footnote={
          <>
            <strong className="text-muted">Pourquoi X Recommandés vs Y Réalisés ?</strong>{' '}
            Les <em>recommandés</em> sont des setups SMC détectés (zones + score) —
            pas des ordres exécutés. Les <em>réalisés</em> viennent du backtester
            smart_money (filtres score/RR/HTF) : souvent beaucoup moins nombreux.
          </>
        }
      />

      {/* Meta bandeau (ex-colonne latérale) — sous les tables de trades */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
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
    </div>
  );
}
