'use client';

/**
 * RPL-001 à RPL-008 — Onglet « Replay » interactif de `/lab`.
 *
 * Rejeu bougie par bougie avec overlay stratégie (markers entrée ▲▼ + sortie ●),
 * contrôles playback (play/pause/step ±1/±10/seek), 7 vitesses (0.5× à MAX),
 * signal log temps réel, stats accumulées, raccourcis clavier.
 *
 * Inspired by `smart-replay-view.tsx` playback pattern, adapté pour les trades
 * de backtest plutôt que les zones SMC.
 *
 * Pour lancer le replay : on appelle `runBacktest` (qui renvoie ohlcv + trades
 * avec bar/exit_bar), puis on rejoue côté client. L'ancien batch multi-TF est
 * conservé dans `MultiTfBatchView` (onglet séparé).
 */

import { useState, useMemo, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { toast } from 'sonner';
import { Loader2, AlertCircle, Play, TrendingUp, Activity, Zap, Eye, Layers, Clock } from 'lucide-react';
import { useRunBacktest, useCancelBacktest, useBacktestSettings } from '@/hooks/use-api';
import { useReplayEngine, type CandleRow } from '@/hooks/use-replay-engine';
import { useReplayKeyboard } from '@/hooks/use-replay-keyboard';
import { monthsToBougies } from '@/lib/limit-hint';
import { ReplayCandlestickChart } from '@/components/charts/replay-candlestick-chart';
import { PlaybackControls } from '@/components/controls/playback-controls';
import { ReplaySignalLog } from '@/components/cards/replay-signal-log';
import { ReplayStatsPanel } from '@/components/cards/replay-stats-panel';
import { normalizeTrade } from '@/lib/backend-normalizers';
import type { BacktestResult, BacktestTrade } from '@/types';

const WELCOME_TIPS = [
  {
    icon: Zap,
    title: 'Vitesse variable',
    text: '0.5× à MAX (batch 100 bougies/frame)',
  },
  {
    icon: Layers,
    title: 'Overlay stratégie',
    text: 'Entrées ▲▼ et sorties ● en temps réel',
  },
  {
    icon: Activity,
    title: 'Contrôle total',
    text: 'Play, pause, pas à pas, scrub',
  },
  {
    icon: Clock,
    title: 'Multi-timeframe',
    text: '1m à 1j, jusqu&apos;à 24 mois',
  },
];

export function ReplayView() {
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState('1h');
  const [months, setMonths] = useState(3);
  const [strategy, setStrategy] = useState<string>('');

  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null);
  const [crosshairCandle, setCrosshairCandle] = useState<CandleRow | null>(null);

  const runBacktest = useRunBacktest();
  const cancelBacktest = useCancelBacktest();
  const { data: settings } = useBacktestSettings();

  // Stratégies disponibles (depuis /api/backtest/settings).
  const availableStrategies: string[] = useMemo(() => {
    return settings?.all_strategies ?? settings?.strategies ?? [];
  }, [settings]);

  // Si pas de stratégie sélectionnée, prends la première activée.
  const effectiveStrategy = strategy || (settings?.strategies_enabled?.[0] ?? availableStrategies[0] ?? '');

  // Convertit le résultat backtest en candles + trades pour le replay.
  const candles: CandleRow[] = useMemo(() => {
    const ohlcv = backtestResult?.ohlcv;
    if (!ohlcv?.time?.length) return [];
    return ohlcv.time.map((t, i) => ({
      time: typeof t === 'number' ? t : Math.floor(new Date(t as string).getTime() / 1000),
      open: ohlcv.open[i] ?? 0,
      high: ohlcv.high[i] ?? 0,
      low: ohlcv.low[i] ?? 0,
      close: ohlcv.close[i] ?? 0,
      volume: ohlcv.volume?.[i],
    }));
  }, [backtestResult]);

  const trades: BacktestTrade[] = useMemo(() => {
    if (!backtestResult?.trades) return [];
    // Si multi-stratégies, filtre par effectiveStrategy si possible.
    const all = backtestResult.trades.map(normalizeTrade);
    if (!effectiveStrategy) return all;
    const filtered = all.filter((t) => t.strategy === effectiveStrategy || t.strategy_name === effectiveStrategy);
    return filtered.length > 0 ? filtered : all;
  }, [backtestResult, effectiveStrategy]);

  const engine = useReplayEngine({ candles, trades });

  useReplayKeyboard({
    onTogglePlay: engine.togglePlay,
    onStep: engine.step,
    onSeek: engine.seekTo,
    onSetSpeed: engine.setSpeed,
    total: engine.total,
    enabled: candles.length > 0,
  });

  const handleLoad = async () => {
    if (!symbol.trim()) {
      toast.error('Symbole requis');
      return;
    }
    if (!effectiveStrategy) {
      toast.error('Sélectionnez une stratégie');
      return;
    }
    // Estime le nombre de bougies à partir des mois + TF.
    const limit = Math.min(8000, Math.max(500, monthsToBougies(months, timeframe)));
    try {
      toast.info(`Chargement des données (${limit} bougies ${timeframe})…`);
      const r = await runBacktest.mutateAsync({
        symbol: symbol.trim(),
        timeframe,
        limit,
        strategies: effectiveStrategy,
      });
      // Si la réponse est un array (multi-strat), prends la première.
      const result = Array.isArray(r) ? r[0] : r;
      setBacktestResult(result);
      if (!result?.ohlcv?.time?.length) {
        toast.warning('Pas de données OHLCV dans la réponse — replay impossible');
      } else {
        toast.success(`${result.ohlcv.time.length} bougies chargées · ${result.trades?.length ?? 0} trades`);
      }
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleCancel = async () => {
    try {
      await cancelBacktest.mutateAsync();
      toast.success('Chargement annulé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const hintBougies = useMemo(() => monthsToBougies(months, timeframe), [months, timeframe]);

  // ── Welcome screen (avant tout chargement) ──────────────────────────────
  if (!backtestResult && !runBacktest.isPending) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Replay interactif</h2>
          <p className="text-sm text-muted mt-1">
            Rejouez le marché bougie par bougie avec overlay stratégie — entrées ▲▼, sorties ●, contrôle playback complet.
          </p>
        </div>

        <ReplayConfigCard
          symbol={symbol}
          setSymbol={setSymbol}
          timeframe={timeframe}
          setTimeframe={setTimeframe}
          months={months}
          setMonths={setMonths}
          strategy={effectiveStrategy}
          setStrategy={setStrategy}
          availableStrategies={availableStrategies}
          hintBougies={hintBougies}
          onLoad={handleLoad}
          isLoading={false}
        />

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {WELCOME_TIPS.map((tip, i) => (
            <Card key={i}>
              <CardContent className="p-4">
                <tip.icon className="h-6 w-6 text-cyan-400 mb-2" />
                <div className="font-semibold text-sm mb-1">{tip.title}</div>
                <div className="text-xs text-muted-foreground">{tip.text}</div>
              </CardContent>
            </Card>
          ))}
        </div>

        <Card>
          <CardContent className="text-center py-12 text-muted-foreground text-sm">
            <TrendingUp className="h-10 w-10 mx-auto mb-3 text-dim" />
            Configurez les paramètres ci-dessus puis cliquez sur « Charger » pour démarrer le replay.
          </CardContent>
        </Card>
      </div>
    );
  }

  // ── Loading ──────────────────────────────────────────────────────────────
  if (runBacktest.isPending && !backtestResult) {
    return (
      <div className="space-y-6">
        <ReplayConfigCard
          symbol={symbol}
          setSymbol={setSymbol}
          timeframe={timeframe}
          setTimeframe={setTimeframe}
          months={months}
          setMonths={setMonths}
          strategy={effectiveStrategy}
          setStrategy={setStrategy}
          availableStrategies={availableStrategies}
          hintBougies={hintBougies}
          onLoad={handleLoad}
          isLoading
          onCancel={handleCancel}
        />
        <Card>
          <CardContent className="flex items-center justify-center gap-3 py-12">
            <Loader2 className="h-6 w-6 animate-spin text-cyan-400" />
            <span className="text-sm text-muted-foreground">Chargement des données en cours…</span>
          </CardContent>
        </Card>
      </div>
    );
  }

  // ── Replay actif ─────────────────────────────────────────────────────────
  return (
    <div className="space-y-4">
      {/* Header + config compactée */}
      <div className="flex items-end justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">
            Replay interactif
            {backtestResult && (
              <span className="ml-2 text-sm text-muted-foreground font-normal">
                {backtestResult.symbol} · {backtestResult.timeframe}
              </span>
            )}
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Espace · ← → · Shift±10 · Home/End · 1/2/5/0 pour vitesses
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setBacktestResult(null);
            engine.reset();
          }}
        >
          ← Nouveau replay
        </Button>
      </div>

      {/* Layout 2 colonnes : sidebar + chart */}
      <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-4">
        {/* Sidebar */}
        <div className="space-y-3">
          <ReplayConfigCard
            symbol={symbol}
            setSymbol={setSymbol}
            timeframe={timeframe}
            setTimeframe={setTimeframe}
            months={months}
            setMonths={setMonths}
            strategy={effectiveStrategy}
            setStrategy={setStrategy}
            availableStrategies={availableStrategies}
            hintBougies={hintBougies}
            onLoad={handleLoad}
            isLoading={runBacktest.isPending}
            onCancel={handleCancel}
            compact
          />
          <ReplayStatsPanel
            stats={engine.accumulated}
            candles={candles}
            position={engine.position}
            dateFrom={backtestResult?.date_from}
            dateTo={backtestResult?.date_to}
          />
        </div>

        {/* Chart + controls + signal log */}
        <div className="space-y-3">
          {/* Chart avec badge OHLCV + position */}
          <Card className="p-0 overflow-hidden relative">
            <div className="relative">
              <ReplayCandlestickChart
                candles={candles}
                position={engine.position}
                trades={trades}
                onCrosshairMove={setCrosshairCandle}
              />
              {/* Badge OHLCV top-left */}
              {crosshairCandle && (
                <div className="absolute top-2 left-2 bg-surface/90 border border-border rounded px-2 py-1 font-mono text-[0.65rem] backdrop-blur">
                  <span className="text-muted-foreground">O</span> {crosshairCandle.open.toFixed(4)}{' '}
                  <span className="text-muted-foreground">H</span> {crosshairCandle.high.toFixed(4)}{' '}
                  <span className="text-muted-foreground">L</span> {crosshairCandle.low.toFixed(4)}{' '}
                  <span className="text-muted-foreground">C</span> {crosshairCandle.close.toFixed(4)}
                  {crosshairCandle.volume != null && (
                    <>
                      {' '}
                      <span className="text-muted-foreground">V</span>{' '}
                      {crosshairCandle.volume.toLocaleString('fr-FR', { maximumFractionDigits: 0 })}
                    </>
                  )}
                </div>
              )}
              {/* Badge position top-right */}
              {engine.total > 0 && (
                <div className="absolute top-2 right-2 bg-surface/90 border border-border rounded px-2 py-1 font-mono text-[0.65rem] backdrop-blur">
                  {engine.position + 1} / {engine.total}
                </div>
              )}
            </div>
          </Card>

          {/* Playback controls */}
          <PlaybackControls
            position={engine.position}
            total={engine.total}
            isPlaying={engine.isPlaying}
            speed={engine.speed}
            onPlayPause={engine.togglePlay}
            onStep={engine.step}
            onSeek={engine.seekTo}
            onSpeedChange={engine.setSpeed}
          />

          {/* Signal log */}
          <ReplaySignalLog entries={engine.signalLog} />
        </div>
      </div>
    </div>
  );
}

// ── Sous-composant : carte de configuration ─────────────────────────────────

interface ReplayConfigCardProps {
  symbol: string;
  setSymbol: (v: string) => void;
  timeframe: string;
  setTimeframe: (v: string) => void;
  months: number;
  setMonths: (v: number) => void;
  strategy: string;
  setStrategy: (v: string) => void;
  availableStrategies: string[];
  hintBougies: number;
  onLoad: () => void;
  isLoading: boolean;
  onCancel?: () => void;
  compact?: boolean;
}

function ReplayConfigCard({
  symbol,
  setSymbol,
  timeframe,
  setTimeframe,
  months,
  setMonths,
  strategy,
  setStrategy,
  availableStrategies,
  hintBougies,
  onLoad,
  isLoading,
  onCancel,
  compact,
}: ReplayConfigCardProps) {
  return (
    <Card>
      <CardHeader className={compact ? 'py-3' : ''}>
        <CardTitle className="text-sm">Configuration</CardTitle>
      </CardHeader>
      <CardContent className={`space-y-3 ${compact ? 'py-3' : ''}`}>
        <div>
          <Label className="text-xs text-dim block mb-1">Symbole</Label>
          <Input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="BTC/USDC"
            className="font-mono text-sm"
          />
        </div>
        <div>
          <Label className="text-xs text-dim block mb-1">Timeframe</Label>
          <TimeframeButtons value={timeframe} onChange={setTimeframe} size="sm" />
        </div>
        <div>
          <Label className="text-xs text-dim block mb-1">
            Mois (1-24) · ≈ {hintBougies.toLocaleString('fr-FR')} bougies
          </Label>
          <input
            type="range"
            min={1}
            max={24}
            value={months}
            onChange={(e) => setMonths(Number(e.target.value))}
            className="w-full accent-cyan-400"
          />
          <div className="text-xs text-muted-foreground font-mono text-center mt-1">{months} mois</div>
        </div>
        <div>
          <Label className="text-xs text-dim block mb-1">Stratégie overlay</Label>
          <Select value={strategy} onValueChange={setStrategy}>
            <SelectTrigger className="text-sm">
              <SelectValue placeholder={strategy || 'Sélectionner…'} />
            </SelectTrigger>
            <SelectContent>
              {availableStrategies.length === 0 ? (
                <SelectItem value="" disabled>
                  Chargement…
                </SelectItem>
              ) : (
                availableStrategies.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
        </div>
        <div className="flex gap-2">
          <Button onClick={onLoad} disabled={isLoading} variant="primary" className="flex-1">
            {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" fill="currentColor" />}
            {isLoading ? 'Chargement…' : 'Charger'}
          </Button>
          {onCancel && (
            <Button onClick={onCancel} disabled={isLoading} variant="ghost" size="sm">
              Annuler
            </Button>
          )}
        </div>
        {isLoading && (
          <div className="flex items-center gap-2 text-xs text-amber-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span>Récupération des bougies…</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
