'use client';

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { useBacktestSettings, useRunBacktest, useCancelBacktest, useBacktestRange } from '@/hooks/use-api';
import { useBacktestStatus, useBacktestSession } from '@/hooks/use-backtest-session';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';
import { Play, Square, Loader2, X } from 'lucide-react';
import { cn, errorMessage } from '@/lib/utils';
import { toDateInputValue, validateDateRange, rangeDurationDays } from '@/lib/backtest-range';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { SymbolSearchInput } from '@/components/ui/symbol-search';
import { StrategyPicker } from '@/components/ui/strategy-picker';
import { BacktestRunningBanner } from '@/components/cards/backtest-running-banner';
import { BacktestProgress } from '@/components/cards/backtest-progress';
import { BacktestResults } from '@/components/views/backtest-results';
import { limitHint } from '@/lib/limit-hint';
import type { BacktestResult } from '@/types';

interface BacktestConfig {
  symbol: string;
  timeframe: string;
  limit: number;
  /** Exclusif de `limit` : dès qu'une date est fournie, le backend ignore `limit`. */
  dateMode: 'bars' | 'range';
  start_date: string;
  end_date: string;
  refresh: boolean;
  walk_forward: boolean;
  monte_carlo: boolean;
  dual_pass: boolean;
  realistic_risk: boolean;
  strategies: string[];
}

export function BacktestView({ expertMode }: { expertMode: boolean }) {
  const settingsQuery = useBacktestSettings();
  const runBacktest = useRunBacktest();
  const cancelBacktest = useCancelBacktest();
  const qc = useQueryClient();
  const { defaultTf, timeframes: activeTfs } = useTradingTimeframes('1h');

  const { data: settings } = settingsQuery;

  const backtestStatus = useBacktestStatus();
  const session = useBacktestSession();
  const [startedAt, setStartedAt] = useState<number | null>(null);

  const [config, setConfig] = useState<BacktestConfig>({
    symbol: 'BTC/USDC',
    timeframe: '1h',
    limit: 500,
    dateMode: 'bars',
    start_date: '',
    end_date: '',
    refresh: false,
    walk_forward: false,
    monte_carlo: false,
    dual_pass: false,
    realistic_risk: false,
    strategies: [],
  });
  const [result, setResult] = useState<BacktestResult | BacktestResult[] | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    if (defaultTf) {
      setConfig((c) =>
        activeTfs.includes(c.timeframe) ? c : { ...c, timeframe: defaultTf },
      );
    }
  }, [defaultTf, activeTfs]);

  useEffect(() => {
    if (!session.restored) return;
    const s = session.restored;
    if (s.result && s.config) {
      setResult(s.result);
      setConfig((c) => ({
        ...c,
        symbol: s.config.symbol ?? c.symbol,
        timeframe: s.config.timeframe ?? c.timeframe,
        limit: s.config.limit ?? c.limit,
        dateMode: s.config.dateMode ?? c.dateMode,
        start_date: s.config.start_date ?? c.start_date,
        end_date: s.config.end_date ?? c.end_date,
        refresh: s.config.refresh ?? c.refresh,
        strategies: s.config.strategies ?? c.strategies,
        walk_forward: s.config.walk_forward ?? c.walk_forward,
        monte_carlo: s.config.monte_carlo ?? c.monte_carlo,
        dual_pass: s.config.dual_pass ?? c.dual_pass,
        realistic_risk: s.config.realistic_risk ?? c.realistic_risk,
      }));
    }
  }, [session.restored]);

  const handleClearSession = () => {
    setResult(null);
    setRunError(null);
    setStartedAt(null);
    session.clear();
    toast.success('Résultat et session effacés');
  };

  const SYMBOL_RE = /^[A-Z0-9]+\/[A-Z0-9]+$|^[A-Z0-9.]+$/;
  const symbolValid = SYMBOL_RE.test(config.symbol.trim());

  const useRange = config.dateMode === 'range';
  const rangeQuery = useBacktestRange(
    config.symbol.trim(), config.timeframe, useRange && symbolValid,
  );
  const available = rangeQuery.data ?? null;
  const rangeCheck = validateDateRange(config.start_date, config.end_date, available);
  const rangeDays = rangeDurationDays(config.start_date, config.end_date);

  const fillMaxRange = () => {
    if (!available?.available) return;
    setConfig((c) => ({
      ...c,
      start_date: toDateInputValue(available.from),
      end_date: toDateInputValue(available.to),
    }));
  };

  const handleRun = async () => {
    if (!symbolValid) {
      toast.error('Symbole invalide — format attendu : BTC/USDC ou BTC');
      return;
    }
    if (config.strategies.length === 0) {
      toast.error('Sélectionnez au moins une stratégie');
      return;
    }
    if (useRange) {
      if (!rangeCheck.ok) {
        toast.error(rangeCheck.error ?? 'Plage de dates invalide');
        return;
      }
      if (rangeCheck.warning) toast.warning(rangeCheck.warning);
    }
    setResult(null);
    setRunError(null);
    setStartedAt(Date.now());
    try {
      const res = await runBacktest.mutateAsync({
        symbol: config.symbol,
        timeframe: config.timeframe,
        ...(useRange
          ? { start_date: config.start_date, end_date: config.end_date }
          : { limit: config.limit }),
        refresh: config.refresh,
        walk_forward: config.walk_forward,
        monte_carlo: config.monte_carlo,
        dual_pass: config.dual_pass,
        realistic_risk: config.realistic_risk,
        strategies: config.strategies.length > 0 ? config.strategies.join(',') : undefined,
      });
      setResult(res);
      session.save(res, {
        symbol: config.symbol,
        timeframe: config.timeframe,
        limit: config.limit,
        dateMode: config.dateMode,
        start_date: config.start_date,
        end_date: config.end_date,
        refresh: config.refresh,
        strategies: config.strategies,
        walk_forward: config.walk_forward,
        monte_carlo: config.monte_carlo,
        dual_pass: config.dual_pass,
        realistic_risk: config.realistic_risk,
      });
      if (config.refresh) {
        qc.invalidateQueries({ queryKey: ['backtestRange'] });
      }
      toast.success('Backtest terminé');
    } catch (e) {
      const msg = errorMessage(e);
      setRunError(msg);
      toast.error(`Erreur : ${msg}`);
    } finally {
      setStartedAt(null);
    }
  };

  const handleCancel = async () => {
    try {
      await cancelBacktest.mutateAsync();
      setStartedAt(null);
      toast.info('Backtest annulé');
    } catch (e) {
      toast.error(`Erreur : ${errorMessage(e)}`);
    }
  };

  const allStrategies = settings?.all_strategies || settings?.strategies || [];
  const enabledStrategies: string[] = settings?.strategies || [];
  const availableStrategies = allStrategies;

  const LIMIT_PRESETS = [500, 2000, 5000, 8000, 50000];
  const isLoading = runBacktest.isPending || !!startedAt;

  return (
    <div className="space-y-4">
      {backtestStatus.running && backtestStatus.startedAt && (
        <BacktestRunningBanner
          startedAt={backtestStatus.startedAt}
          onCancel={handleCancel}
        />
      )}

      {/* Config panel — pleine largeur, comme Optimizer */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <Label htmlFor="bt-symbol">Symbole</Label>
            <SymbolSearchInput
              id="bt-symbol"
              value={config.symbol}
              onChange={(s) => setConfig({ ...config, symbol: s })}
            />
            {!symbolValid && (
              <p id="bt-symbol-help" className="text-[10px] text-rose-400 mt-1" role="alert">
                Format invalide — attendu « BASE/QUOTE » (ex. BTC/USDC) ou ticker (ex. AIR.PA).
              </p>
            )}
          </div>

          <div>
            <Label>Timeframe</Label>
            <TimeframeButtons
              value={config.timeframe}
              onChange={(v) => setConfig({ ...config, timeframe: v })}
            />
          </div>

          <div>
            <Label>Période analysée</Label>
            <div
              role="radiogroup"
              aria-label="Mode de sélection de la période"
              className="grid grid-cols-2 gap-1 mt-1 p-0.5 bg-surface border border-border rounded-md"
            >
              {([
                { key: 'bars', label: 'Dernières bougies' },
                { key: 'range', label: 'Plage de dates' },
              ] as const).map((m) => (
                <button
                  key={m.key}
                  type="button"
                  role="radio"
                  aria-checked={config.dateMode === m.key}
                  onClick={() => setConfig({ ...config, dateMode: m.key })}
                  className={cn(
                    'px-2 py-1 rounded text-[11px] transition-colors',
                    config.dateMode === m.key
                      ? 'bg-cyan-500/20 text-cyan-800 dark:text-cyan-300'
                      : 'text-muted hover:text-foreground',
                  )}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          {useRange ? (
            <div>
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="bt-start-date">Du / au</Label>
                <button
                  type="button"
                  onClick={fillMaxRange}
                  disabled={!available?.available}
                  className={cn(
                    'px-2 py-0.5 rounded text-[10px] border transition-colors',
                    available?.available
                      ? 'bg-surface border-border text-muted hover:text-foreground hover:border-border-hi'
                      : 'bg-surface border-border text-dim cursor-not-allowed opacity-50',
                  )}
                >
                  Max disponible
                </button>
              </div>
              <div className="grid grid-cols-2 gap-2 mt-1">
                <Input
                  id="bt-start-date"
                  type="date"
                  aria-label="Date de début"
                  value={config.start_date}
                  min={toDateInputValue(available?.from) || undefined}
                  max={toDateInputValue(available?.to) || undefined}
                  onChange={(e) => setConfig({ ...config, start_date: e.target.value })}
                />
                <Input
                  id="bt-end-date"
                  type="date"
                  aria-label="Date de fin"
                  value={config.end_date}
                  min={toDateInputValue(available?.from) || undefined}
                  max={toDateInputValue(available?.to) || undefined}
                  onChange={(e) => setConfig({ ...config, end_date: e.target.value })}
                />
              </div>

              {rangeQuery.isLoading && (
                <p className="text-[10px] text-dim mt-1">Lecture du cache…</p>
              )}
              {available?.available && (
                <p className="text-[10px] text-dim mt-1">
                  Cache : {toDateInputValue(available.from)} → {toDateInputValue(available.to)}
                  {' '}· {available.bars.toLocaleString('fr-FR')} bougies
                </p>
              )}

              {rangeCheck.error ? (
                <p className="text-[10px] text-rose-400 mt-1" role="alert">
                  {rangeCheck.error}
                </p>
              ) : rangeCheck.warning ? (
                <p className="text-[10px] text-amber-400 mt-1">{rangeCheck.warning}</p>
              ) : rangeDays ? (
                <p className="text-[10px] text-emerald-400 mt-1">
                  {rangeDays.toLocaleString('fr-FR')} jour{rangeDays > 1 ? 's' : ''} couvert
                  {rangeDays > 1 ? 's' : ''}
                </p>
              ) : (
                <p className="text-[10px] text-dim mt-1">
                  Une borne vide s&apos;étend jusqu&apos;au bout du cache.
                </p>
              )}
            </div>
          ) : (
          <div>
            <Label htmlFor="bt-limit">Nombre de bougies</Label>
            <Input
              id="bt-limit"
              type="number"
              min={100}
              max={50000}
              step={100}
              value={config.limit}
              onChange={(e) => setConfig({ ...config, limit: Number(e.target.value) })}
            />
            <div className="flex flex-wrap gap-1 mt-1">
              {LIMIT_PRESETS.map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setConfig({ ...config, limit: n })}
                  className={cn(
                    'px-2 py-0.5 rounded text-[10px] border transition-colors',
                    config.limit === n
                      ? 'bg-cyan-500/20 text-cyan-800 dark:text-cyan-300 border-cyan-500/40'
                      : 'bg-surface border-border text-muted hover:text-foreground hover:border-border-hi',
                  )}
                >
                  {n >= 1000 ? `${n / 1000}k` : n}
                </button>
              ))}
            </div>
            {(() => {
              const h = limitHint(config.limit, config.timeframe);
              const toneClass = h.tone === 'green'
                ? 'text-emerald-400'
                : h.tone === 'amber'
                  ? 'text-amber-400'
                  : 'text-rose-400';
              return (
                <p className={cn('text-[10px] mt-1', toneClass)}>{h.text}</p>
              );
            })()}
          </div>
          )}
          </div>

          <div className="flex items-start gap-2 text-xs">
            <Switch
              id="bt-refresh"
              aria-label="Rafraîchir les données avant le backtest"
              checked={config.refresh}
              onCheckedChange={(v) => setConfig({ ...config, refresh: v })}
            />
            <label htmlFor="bt-refresh" className="cursor-pointer leading-tight">
              Rafraîchir les données
              <span className="block text-dim text-[10px] mt-0.5">
                Complète le cache local depuis l&apos;exchange avant de lancer. Ajoute
                un appel réseau — à activer pour backtester jusqu&apos;à maintenant.
              </span>
            </label>
          </div>

          <StrategyPicker
            strategies={availableStrategies}
            value={config.strategies}
            onChange={(strategies) => setConfig((c) => ({ ...c, strategies }))}
            enabled={enabledStrategies}
          />

          {/* Mode expert : options avancées */}
          {expertMode && (
            <div className="space-y-3 pt-2 border-t border-border">
              <div className="text-[10px] uppercase tracking-wider text-dim font-semibold">
                Options avancées
              </div>
              {/* Radix Switch = button : aria-label requis (pas de <label> natif). */}
              <div className="flex items-center gap-2 text-xs">
                <Switch
                  id="bt-walk-forward"
                  aria-label="Walk-Forward Analysis"
                  checked={config.walk_forward}
                  onCheckedChange={(v) => setConfig({ ...config, walk_forward: v })}
                />
                <label htmlFor="bt-walk-forward" className="cursor-pointer">
                  Walk-Forward Analysis
                </label>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <Switch
                  id="bt-monte-carlo"
                  aria-label="Monte-Carlo (200 runs)"
                  checked={config.monte_carlo}
                  onCheckedChange={(v) => setConfig({ ...config, monte_carlo: v })}
                />
                <label htmlFor="bt-monte-carlo" className="cursor-pointer">
                  Monte-Carlo (200 runs)
                </label>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <Switch
                  id="bt-dual-pass"
                  aria-label="Étude vs Réel (double passe)"
                  checked={config.dual_pass}
                  onCheckedChange={(v) => setConfig({ ...config, dual_pass: v })}
                />
                <label htmlFor="bt-dual-pass" className="cursor-pointer">
                  Étude vs Réel
                  <span className="text-dim ml-1">(double le temps de calcul)</span>
                </label>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <Switch
                  id="bt-realistic-risk"
                  aria-label="Mode realistic_risk (circuit breakers)"
                  checked={config.realistic_risk}
                  onCheckedChange={(v) => setConfig({ ...config, realistic_risk: v })}
                />
                <label htmlFor="bt-realistic-risk" className="cursor-pointer">
                  Mode realistic_risk
                  <span className="text-dim ml-1">
                    (circuit breakers : 3 pertes consécutives → pause, DD journalier, kill-switch)
                  </span>
                </label>
              </div>
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <Button
              onClick={handleRun}
              disabled={
                isLoading || !symbolValid || backtestStatus.running
                || config.strategies.length === 0
                || (useRange && !rangeCheck.ok)
              }
              className="flex-1"
            >
              {isLoading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              Analyser
            </Button>
            {isLoading && (
              <Button variant="danger" onClick={handleCancel}>
                <Square className="w-4 h-4" />
              </Button>
            )}
            {result && !isLoading && (
              <Button
                variant="outline"
                onClick={handleClearSession}
                title="Efface le résultat affiché et la session sauvegardée"
              >
                <X className="w-4 h-4" />
                Effacer
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Results panel */}
      <div className="space-y-4">
        {!result && !isLoading && (
          <Card>
            <CardContent className="flex items-center justify-center min-h-[300px] text-muted text-sm">
              Configure et lance un backtest pour voir les résultats
            </CardContent>
          </Card>
        )}

        {isLoading && (
          <BacktestProgress
            startedAt={startedAt ?? Date.now()}
            strategies={config.strategies}
            walkForward={config.walk_forward}
            monteCarlo={config.monte_carlo}
            onCancel={handleCancel}
          />
        )}

        {!result && runError && !isLoading && (
          <BacktestResults error={runError} />
        )}
        {result && <BacktestResults result={result} scoreThreshold={settings?.score_threshold} />}
      </div>
    </div>
  );
}
