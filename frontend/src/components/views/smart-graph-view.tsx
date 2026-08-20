'use client';

/**
 * Onglet Smart Graph de `/market` — chart candlestick full-width + overlays SMC.
 * Meta (signal, bias, PD…) en bandeau sous « Plans recommandés ».
 */

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { errorMessage } from '@/lib/utils';
import { useSMC, useSMCReplay } from '@/hooks/use-api';
import {
  TradePlansTable, RealizedTradesTable, type TradePlan, type RealizedTrade,
} from '@/components/cards/trade-plans-table';
import { FastAnalysisPanel, type FastAnalysisResult } from '@/components/cards/fast-analysis-panel';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { SymbolSearchInput } from '@/components/ui/symbol-search';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { useSmartGraphChart } from '@/hooks/use-smart-graph-chart';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import {
  Loader2, RefreshCw, AlertCircle, Activity, Search, X,
  ArrowUp, ArrowDown, Target, Shield, Layers, Droplets, Waves, GitBranch, Sparkles,
  Box, Ban, BarChart3, TrendingUp, Recycle, Spline, Flame, CircleDot,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import {
  formatPrice, normalizePd, type OverlayToggles, type ChartIndicators,
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

  const { containerRef, rsiContainerRef, macdContainerRef } = useSmartGraphChart({
    data, selectedPlan, toggles, indicators, showEma, showBb, showRsi, showMacd,
  });

  useEffect(() => {
    setSelectedPlan(null);
  }, [symbol, timeframe]);

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

  const toggleList: Array<[keyof OverlayToggles, string, LucideIcon]> = [
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
            <SymbolSearchInput value={symbol} onChange={setSymbol} id="smart-graph-symbol" />
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
            <span>Erreur: {errorMessage(error)}</span>
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

      {/* Panneaux RSI / MACD — cases à cocher en haut + croix pour masquer */}
      {showRsi && (
        <Card className="p-0 overflow-hidden">
          <div className="flex items-center justify-between px-2 pt-1">
            <div className="text-[9px] text-dim">RSI</div>
            <button
              type="button"
              aria-label="Masquer RSI"
              className="p-0.5 rounded text-muted hover:text-foreground hover:bg-card-hover"
              onClick={() => setShowRsi(false)}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
          <div ref={rsiContainerRef} className="w-full h-[100px]" />
        </Card>
      )}
      {showMacd && (
        <Card className="p-0 overflow-hidden">
          <div className="flex items-center justify-between px-2 pt-1">
            <div className="text-[9px] text-dim">MACD</div>
            <button
              type="button"
              aria-label="Masquer MACD"
              className="p-0.5 rounded text-muted hover:text-foreground hover:bg-card-hover"
              onClick={() => setShowMacd(false)}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
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
              plans={(data?.trade_plans || []) as TradePlan[]}
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
                  ? (replayData?.trades || []).find((t: RealizedTrade) =>
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
                {(vp.hvns?.length ?? 0) > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {vp.hvns?.slice(0, 4).map((h: number, i: number) => (
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

      {!isLoading && !isError && data && <SmartGraphTables data={data as Parameters<typeof SmartGraphTables>[0]['data']} />}
    </div>
  );
}
