'use client';

/**
 * S5-F3-US1 — Page Laboratoire (fusion Backtest + Optimizer + ML + Replay + Compare).
 *
 * Stratégie strangler fig : page unifiée qui coexiste avec les pages existantes.
 *
 * Pipeline guidé : « Analyser » → verdict en clair → bouton unique « Créer le bot (Essai) »
 * Mode expert opt-in (toggle dans /settings) qui révèle les options avancées.
 *
 * Tabs : Backtest / Optimizer / ML Train / Replay / Compare
 *
 * Backend consommé :
 *  - /api/backtest (+ settings)
 *  - /api/optimize/* (start, status, stream SSE, apply, cancel, delete, results, spaces)
 *  - /api/ml/* (recipes, registry, train, sweep)
 *  - /api/replay
 *  - /api/backtest (pour compare)
 *
 * Lot Laboratoire — les onglets Optimizer / ML / Replay / Compare montent
 * désormais le contenu réel des anciennes pages (`src/components/views/`) et
 * non plus des cartes de renvoi. `/optimizer`, `/ml`, `/replay` et `/compare`
 * sont en 308 vers `/lab?tab=…` (cf. `next.config.mjs`).
 */

import { useState, useEffect, Suspense } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useSearchParams, useRouter } from 'next/navigation';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { QueryBoundary } from '@/components/ui/query-state';
import { CsvExportButton, JsonExportButton } from '@/components/ui/export-buttons';
import { WalkForwardTable } from '@/components/charts/walk-forward-table';
import { StudyVsLiveCard } from '@/components/cards/study-vs-live-card';
import { MonteCarloPanel } from '@/components/charts/monte-carlo-panel';
import { TradesScatter } from '@/components/charts/trades-scatter';
import { BacktestEquityChart } from '@/components/charts/backtest-equity-chart';
import { CostModelCard } from '@/components/cards/cost-model-card';
import { useBacktestSettings, useRunBacktest, useCancelBacktest, useBacktestRange } from '@/hooks/use-api';
import { useConfig, usePresets, useSetExpertMode } from '@/hooks/use-api';
import { useBacktestStatus, useBacktestSession } from '@/hooks/use-backtest-session';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';
import {
  Play, Square, Loader2, FlaskConical, Sparkles, Brain, Repeat,
  GitCompare, AlertCircle, CheckCircle2, TrendingUp, Rocket, Archive,
  Maximize2, FileDown, X, Zap, Layers, Shield,
} from 'lucide-react';
import { cn, formatUSD, formatPct } from '@/lib/utils';
import { toDateInputValue, validateDateRange, rangeDurationDays } from '@/lib/backtest-range';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { PriceSignalsChart } from '@/components/charts/price-signals-chart';
import { TradesTable } from '@/components/tables/trades-table';
import { DiagnosticsPanel } from '@/components/cards/diagnostics-panel';
import { RecommendationsPanel } from '@/components/cards/recommendations-panel';
import { BacktestRunningBanner } from '@/components/cards/backtest-running-banner';
import { BacktestProgress } from '@/components/cards/backtest-progress';
import { StrategyComparisonTable } from '@/components/cards/strategy-comparison-table';
import { TradesStatsPanel } from '@/components/cards/trades-stats-panel';
import { MLBacktestPanel } from '@/components/cards/ml-backtest-panel';
import { CostSimulatorPanel } from '@/components/cards/cost-simulator-panel';
import { limitHint } from '@/lib/limit-hint';
import { recommendedThreshold } from '@/lib/strat-thresholds';
import {
  normalizeDiagnostics, equityFinal, buyHold,
} from '@/lib/backend-normalizers';

/*
  Les 4 vues portées valent ~1 840 lignes et tirent recharts (Replay, Compare)
  ou un flux SSE (Optimizer). Radix ne monte que l'onglet actif : `dynamic`
  aligne le coût réseau sur ce comportement plutôt que de faire payer les
  quatre à qui n'ouvre que Backtest.
*/
const tabLoading = () => <div className="p-8 text-center text-sm text-muted">Chargement…</div>;

const OptimizerView = dynamic(
  () => import('@/components/views/optimizer-view').then((m) => m.OptimizerView),
  { loading: tabLoading },
);
const MLView = dynamic(
  () => import('@/components/views/ml-view').then((m) => m.MLView),
  { loading: tabLoading },
);
const ReplayView = dynamic(
  () => import('@/components/views/replay-view').then((m) => m.ReplayView),
  { loading: tabLoading },
);
const MultiTfBatchView = dynamic(
  () => import('@/components/views/multi-tf-batch-view').then((m) => m.MultiTfBatchView),
  { loading: tabLoading },
);
const CompareView = dynamic(
  () => import('@/components/views/compare-view').then((m) => m.CompareView),
  { loading: tabLoading },
);

const TABS = ['backtest', 'optimizer', 'ml', 'replay', 'batch', 'compare'] as const;

export default function LabPage() {
  return (
    <Suspense fallback={<div className="p-6 text-muted">Chargement…</div>}>
      <LabContent />
    </Suspense>
  );
}

function LabContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  // Un `?tab=` inconnu (ancien favori, faute de frappe) retombe sur Backtest
  // plutôt que d'afficher une page d'onglets vide.
  const requestedTab = searchParams.get('tab');
  const initialTab = TABS.includes(requestedTab as (typeof TABS)[number])
    ? requestedTab!
    : 'backtest';
  const intent = searchParams.get('intent'); // 'create' si arrivé depuis /bots
  const [tab, setTab] = useState(initialTab);

  // Mode expert — source de vérité = le backend (`/api/settings/presets`), comme
  // sur /settings. Cette page lisait auparavant uniquement `localStorage`, via
  // un `useState(initializer)` détourné en effet : le flag divergeait dès que le
  // mode expert était basculé depuis /settings (qui n'écrit que côté backend), et
  // repartait à `false` sur un autre navigateur. localStorage n'est plus qu'un
  // cache d'affichage le temps que la requête réponde.
  const presetsQuery = usePresets();
  const setExpertModeMutation = useSetExpertMode();
  const [localExpert, setLocalExpert] = useState(false);
  useEffect(() => {
    setLocalExpert(localStorage.getItem('expert_mode') === 'true');
  }, []);
  const expertMode = presetsQuery.data ? !!presetsQuery.data.expert_mode : localExpert;

  const handleExpertToggle = async (checked: boolean) => {
    setLocalExpert(checked);
    localStorage.setItem('expert_mode', String(checked));
    try {
      await setExpertModeMutation.mutateAsync(checked);
    } catch (e: any) {
      toast.error(`Mode expert non enregistré : ${e.message}`);
    }
  };

  const header = (
    <div className="flex items-end justify-between flex-wrap gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Laboratoire</h1>
        <p className="text-sm text-muted mt-1">
          Analyse, optimise et entraîne tes stratégies · workflow guidé en verdict clair
        </p>
      </div>
      <div className="flex items-center gap-3">
        {intent === 'create' && (
          <Badge variant="info" className="text-xs">
            <Rocket className="w-3 h-3" />
            Nouveau bot
          </Badge>
        )}
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <Switch
            checked={expertMode}
            onCheckedChange={handleExpertToggle}
            aria-label="Mode expert"
          />
          <span className="text-muted">Mode expert</span>
        </label>
      </div>
    </div>
  );

  return (
    <div className="space-y-6 animate-fade-in">
      {header}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-6 w-full max-w-3xl">
          <TabsTrigger value="backtest">
            <FlaskConical className="w-3.5 h-3.5 mr-1.5" />
            Backtest
          </TabsTrigger>
          <TabsTrigger value="optimizer">
            <Sparkles className="w-3.5 h-3.5 mr-1.5" />
            Optimizer
          </TabsTrigger>
          <TabsTrigger value="ml">
            <Brain className="w-3.5 h-3.5 mr-1.5" />
            ML Train
          </TabsTrigger>
          <TabsTrigger value="replay">
            <Repeat className="w-3.5 h-3.5 mr-1.5" />
            Replay
          </TabsTrigger>
          <TabsTrigger value="batch">
            <Layers className="w-3.5 h-3.5 mr-1.5" />
            Multi-TF
          </TabsTrigger>
          <TabsTrigger value="compare">
            <GitCompare className="w-3.5 h-3.5 mr-1.5" />
            Compare
          </TabsTrigger>
        </TabsList>

        <TabsContent value="backtest">
          <BacktestTab expertMode={expertMode} />
        </TabsContent>
        <TabsContent value="optimizer">
          <OptimizerView />
        </TabsContent>
        <TabsContent value="ml">
          <MLTab />
        </TabsContent>
        <TabsContent value="replay">
          <ReplayView />
        </TabsContent>
        <TabsContent value="batch">
          <MultiTfBatchView />
        </TabsContent>
        <TabsContent value="compare">
          <CompareView />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── Backtest Tab ──────────────────────────────────────────────────────────

interface BacktestConfig {
  symbol: string;
  timeframe: string;
  limit: number;
  /** QW-2 — comment borner la période analysée.
   *
   * Les deux modes sont EXCLUSIFS, parce que le backend l'est : dès qu'une
   * borne de date est fournie, `limit` est ignoré (forcé au maximum, puis le
   * DataFrame est filtré). Afficher les deux réglages ensemble laisserait
   * croire qu'ils se combinent. */
  dateMode: 'bars' | 'range';
  /** QW-2 — borne basse, format `yyyy-mm-dd` (vide = début du cache). */
  start_date: string;
  /** QW-2 — borne haute, format `yyyy-mm-dd`, inclusive (vide = fin du cache). */
  end_date: string;
  walk_forward: boolean;
  monte_carlo: boolean;
  dual_pass: boolean;
  /** QW-6 — active les circuit breakers (consecutive losses, slot daily DD,
   * max trades/day, volatility brake, kill-switch global) dans le backtest.
   * Opt-in pour préserver la parité avec les backtests existants. */
  realistic_risk: boolean;
  strategies: string[];
}

function BacktestTab({ expertMode }: { expertMode: boolean }) {
  const settingsQuery = useBacktestSettings();
  const runBacktest = useRunBacktest();
  const cancelBacktest = useCancelBacktest();
  const qc = useQueryClient();
  const { defaultTf, timeframes: activeTfs } = useTradingTimeframes('1h');

  const { data: settings } = settingsQuery;

  // BT-004 — sync serveur + persistance session du dernier backtest.
  // Poll `/api/backtest/status` toutes les 5 s pour détecter un run déjà actif
  // côté serveur (autre onglet, post-reload). Restore le dernier résultat
  // depuis `sessionStorage` au mount (TTL 30 min) pour ne pas le perdre sur un
  // reload accidentel pendant une analyse.
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
    walk_forward: false,
    monte_carlo: false,
    dual_pass: false,
    realistic_risk: false,
    strategies: [],
  });
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    if (defaultTf) {
      setConfig((c) =>
        activeTfs.includes(c.timeframe) ? c : { ...c, timeframe: defaultTf },
      );
    }
  }, [defaultTf, activeTfs]);

  // BT-004 — restauration du dernier résultat depuis sessionStorage.
  useEffect(() => {
    if (!session.restored) return;
    const s = session.restored;
    // Ne restaurer que si le config et le résultat sont compatibles
    // (mêmes stratégies + symbole + timeframe, pour ne pas afficher un
    // résultat qui ne correspond pas à la config courante).
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
        strategies: s.config.strategies ?? c.strategies,
        walk_forward: s.config.walk_forward ?? c.walk_forward,
        monte_carlo: s.config.monte_carlo ?? c.monte_carlo,
        dual_pass: s.config.dual_pass ?? c.dual_pass,
        realistic_risk: s.config.realistic_risk ?? c.realistic_risk,
      }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.restored]);

  // BT-004 — purge : vide le résultat affiché ET la session persistée.
  const handleClearSession = () => {
    setResult(null);
    setStartedAt(null);
    session.clear();
    toast.success('Résultat et session effacés');
  };

  // BT-013 — validation du symbole : `BASE/QUOTE` ou token simple (ex. BTC).
  // Pattern ensembliste : valide `BTC/USDC`, `ETH/USDT`, `SOL`, `BNB.USDT`.
  const SYMBOL_RE = /^[A-Z0-9]+\/[A-Z0-9]+$|^[A-Z0-9.]+$/;
  const symbolValid = SYMBOL_RE.test(config.symbol.trim());

  // QW-2 — plage de dates. La requête n'est lancée qu'en mode « range » (et
  // sur un symbole valide) : inutile d'interroger le cache tant que
  // l'utilisateur raisonne en nombre de bougies.
  const useRange = config.dateMode === 'range';
  const rangeQuery = useBacktestRange(
    config.symbol.trim(), config.timeframe, useRange && symbolValid,
  );
  const available = rangeQuery.data ?? null;
  const rangeCheck = validateDateRange(config.start_date, config.end_date, available);
  const rangeDays = rangeDurationDays(config.start_date, config.end_date);

  /** Bouton « Max disponible » — recopie les bornes du cache dans le formulaire. */
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
    if (useRange) {
      if (!rangeCheck.ok) {
        toast.error(rangeCheck.error ?? 'Plage de dates invalide');
        return;
      }
      if (rangeCheck.warning) toast.warning(rangeCheck.warning);
    }
    setResult(null);
    setStartedAt(Date.now());
    try {
      const res = await runBacktest.mutateAsync({
        symbol: config.symbol,
        timeframe: config.timeframe,
        // Les deux modes s'excluent : n'envoyer que les paramètres du mode
        // actif évite que le backend arbitre à notre place.
        ...(useRange
          ? { start_date: config.start_date, end_date: config.end_date }
          : { limit: config.limit }),
        walk_forward: config.walk_forward,
        monte_carlo: config.monte_carlo,
        dual_pass: config.dual_pass,
        realistic_risk: config.realistic_risk,
        strategies: config.strategies.length > 0 ? config.strategies.join(',') : undefined,
      });
      setResult(res);
      // BT-004 — persister le résultat pour restauration post-reload.
      session.save(res, {
        symbol: config.symbol,
        timeframe: config.timeframe,
        limit: config.limit,
        dateMode: config.dateMode,
        start_date: config.start_date,
        end_date: config.end_date,
        strategies: config.strategies,
        walk_forward: config.walk_forward,
        monte_carlo: config.monte_carlo,
        dual_pass: config.dual_pass,
        realistic_risk: config.realistic_risk,
      });
      toast.success('Backtest terminé');
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    } finally {
      setStartedAt(null);
    }
  };

  const handleCancel = async () => {
    try {
      await cancelBacktest.mutateAsync();
      setStartedAt(null);
      toast.info('Backtest annulé');
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  const toggleStrategy = (s: string) => {
    setConfig((c) => ({
      ...c,
      strategies: c.strategies.includes(s)
        ? c.strategies.filter((x) => x !== s)
        : [...c.strategies, s],
    }));
  };

  // BT-014 — la liste déroulante montre TOUTES les stratégies disponibles
  // (settings.all_strategies) ; le badge `● actif` marque celles activées en
  // live (settings.strategies = config.strategies.enabled).
  const allStrategies = settings?.all_strategies || settings?.strategies || [];
  const enabledStrategies: string[] = settings?.strategies || [];
  const availableStrategies = allStrategies;

  // BT-012 — presets rapides de limite de bougies.
  const LIMIT_PRESETS = [500, 2000, 5000, 8000];

  // BT-005 — la progression simulée remplace le spinner muet. C'est la même
  // information qu'avant (run en cours) avec en plus ETA + log horodaté.
  const isLoading = runBacktest.isPending || !!startedAt;
  const isRunning = isLoading || backtestStatus.running;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* BT-004 — banner si un backtest tourne déjà côté serveur (autre onglet). */}
      {backtestStatus.running && backtestStatus.startedAt && (
        <div className="lg:col-span-3">
          <BacktestRunningBanner
            startedAt={backtestStatus.startedAt}
            onCancel={handleCancel}
          />
        </div>
      )}

      {/* Config panel */}
      <Card className="lg:col-span-1">
        <CardHeader>
          <CardTitle className="text-sm">Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="bt-symbol">Symbole</Label>
            <Input
              id="bt-symbol"
              value={config.symbol}
              onChange={(e) => setConfig({ ...config, symbol: e.target.value.toUpperCase() })}
              placeholder="BTC/USDC"
              className="font-mono"
              pattern="^[A-Z0-9]+/[A-Z0-9]+$|^[A-Z0-9.]+$"
              aria-invalid={!symbolValid}
              aria-describedby="bt-symbol-help"
            />
            {/* BT-013 — message d'erreur si le symbole ne match pas le pattern. */}
            {!symbolValid && (
              <p id="bt-symbol-help" className="text-[10px] text-rose-400 mt-1" role="alert">
                Format invalide — attendu « BASE/QUOTE » (ex. BTC/USDC) ou token simple (ex. BTC).
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

          {/* QW-2 — période analysée : deux modes exclusifs (cf. BacktestConfig). */}
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
                      ? 'bg-cyan-500/20 text-cyan-300'
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

              {/* État du cache : ce que l'utilisateur peut réellement demander. */}
              {rangeQuery.isLoading && (
                <p className="text-[10px] text-dim mt-1">Lecture du cache…</p>
              )}
              {available?.available && (
                <p className="text-[10px] text-dim mt-1">
                  Cache : {toDateInputValue(available.from)} → {toDateInputValue(available.to)}
                  {' '}· {available.bars.toLocaleString('fr-FR')} bougies
                </p>
              )}

              {/* Erreur bloquante, avertissement, ou durée — jamais les trois. */}
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
            {/* BT-012 — preset rapides + hint conversion bougies → durée. */}
            <div className="flex flex-wrap gap-1 mt-1">
              {LIMIT_PRESETS.map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setConfig({ ...config, limit: n })}
                  className={cn(
                    'px-2 py-0.5 rounded text-[10px] border transition-colors',
                    config.limit === n
                      ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
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

          {/* Stratégies */}
          <div>
            <Label>Stratégies ({config.strategies.length} sélectionnées)</Label>
            <div className="max-h-32 overflow-y-auto space-y-1 mt-1 border border-border rounded-md p-2">
              {availableStrategies.length === 0 ? (
                <p className="text-xs text-dim">Chargement…</p>
              ) : (
                availableStrategies.map((s: string) => {
                  const isEnabled = enabledStrategies.includes(s);
                  return (
                    <label
                      key={s}
                      className="flex items-center gap-2 text-xs cursor-pointer hover:bg-card-hover rounded px-1 py-0.5"
                    >
                      <input
                        type="checkbox"
                        checked={config.strategies.includes(s)}
                        onChange={() => toggleStrategy(s)}
                      />
                      <span className="font-mono">{s}</span>
                      {isEnabled && (
                        <Badge variant="success" className="text-[0.55rem] px-1 py-0 ml-auto">
                          ● actif
                        </Badge>
                      )}
                    </label>
                  );
                })
              )}
            </div>
          </div>

          {/* Mode expert : options avancées */}
          {expertMode && (
            <div className="space-y-3 pt-2 border-t border-border">
              <div className="text-[10px] uppercase tracking-wider text-dim font-semibold">
                Options avancées
              </div>
              {/*
                Radix `Switch` rend un <button role="switch"> : l'englober dans
                un <label> ne lui donne AUCUN nom accessible (label/htmlFor ne
                nomme que les contrôles de formulaire natifs). Ces deux
                interrupteurs étaient donc anonymes pour un lecteur d'écran —
                violation axe `button-name`, de gravité critique, sur une page
                couverte par le job a11y désormais bloquant.
              */}
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
              {/* S12 §5.1 — coûte un run complet de plus : opt-in explicite,
                  au même titre que le walk-forward. */}
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
              {/* QW-6 — Mode realistic_risk : active les circuit breakers
                  (consecutive losses, slot daily DD, max trades/day, vol brake,
                  kill-switch global) pour évaluer la robustesse face aux
                  pauses de slot. Opt-in pour préserver la parité avec les
                  backtests existants. */}
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
            {/* BT-004 — purge de la session persistée. Sans ce bouton, un
                résultat restauré depuis `sessionStorage` restait affiché
                jusqu'à expiration du TTL (30 min) sans moyen de s'en défaire. */}
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
      <div className="lg:col-span-2 space-y-4">
        {!result && !isLoading && (
          <Card>
            <CardContent className="flex items-center justify-center min-h-[300px] text-muted text-sm">
              Configure et lance un backtest pour voir les résultats
            </CardContent>
          </Card>
        )}

        {/* BT-005 — carte de progression avec ETA + log horodaté, qui remplace
            le spinner muet. Les étapes sont simulées côté client : le backend
            ne stream pas la progression, mais le per-strategy runtime est
            suffisamment long (>1s) pour que l'ETA soit utile. */}
        {isLoading && (
          <BacktestProgress
            startedAt={startedAt ?? Date.now()}
            strategies={config.strategies}
            walkForward={config.walk_forward}
            monteCarlo={config.monte_carlo}
            onCancel={handleCancel}
          />
        )}

        {result && <BacktestResults result={result} scoreThreshold={settings?.score_threshold} />}
      </div>
    </div>
  );
}

// ── Verdict en clair ─────────────────────────────────────────────────────

function Verdict({ result }: { result: any }) {
  // Si results est un tableau (multi-stratégies), prendre la première
  const r = Array.isArray(result) ? result[0] : result;
  const byStrategy = r?.by_strategy || {};
  const strategyNames = Object.keys(byStrategy);

  if (strategyNames.length === 0) {
    return (
      <Card className="border-amber-500/30 bg-amber-500/5">
        <CardContent className="p-4 flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-amber-400" />
          <p className="text-sm">
            <span className="font-medium">Pas de trades générés. </span>
            <span className="text-muted">Ajuste la période ou les stratégies sélectionnées.</span>
          </p>
        </CardContent>
      </Card>
    );
  }

  // Trouve la meilleure stratégie
  const bestEntry = strategyNames
    .map((name) => [name, byStrategy[name]] as const)
    .sort(([, a]: any, [, b]: any) => (b.total_pnl ?? 0) - (a.total_pnl ?? 0))[0];
  const [bestName, bestStats] = bestEntry;
  const pnl = bestStats.total_pnl ?? 0;
  const wr = bestStats.win_rate ?? 0;
  const sharpe = bestStats.sharpe ?? 0;
  const maxDd = bestStats.max_drawdown ?? 0;
  const trades = bestStats.total_trades ?? 0;

  // Verdict logic
  const isPositive = pnl > 0;
  const isStrong = sharpe > 1.5 && trades >= 20 && wr >= 50;
  const isRisky = maxDd < -20 || trades < 10;

  let tone: 'positive' | 'neutral' | 'negative' = 'neutral';
  let icon: React.ReactNode = <AlertCircle className="w-5 h-5 text-amber-400" />;
  let message = '';

  if (isStrong && !isRisky) {
    tone = 'positive';
    icon = <CheckCircle2 className="w-5 h-5 text-emerald-400" />;
    message = `Edge significatif sur ${bestName} avec ${trades} trades, Sharpe ${sharpe.toFixed(2)}, max DD ${maxDd.toFixed(1)}%. Recommandation : essai avec budget 5%.`;
  } else if (isPositive && !isRisky) {
    tone = 'neutral';
    icon = <TrendingUp className="w-5 h-5 text-cyan-400" />;
    message = `Résultats positifs sur ${bestName} (${formatUSD(pnl, { sign: true })}, WR ${wr.toFixed(0)}%) mais edge limité. À valider par forward-test.`;
  } else if (isRisky) {
    tone = 'negative';
    icon = <AlertCircle className="w-5 h-5 text-amber-400" />;
    message = `Résultats risqués sur ${bestName} : ${trades} trades (${trades < 10 ? 'trop peu' : 'ok'}), max DD ${maxDd.toFixed(1)}%. À éviter en l'état.`;
  } else {
    tone = 'negative';
    icon = <AlertCircle className="w-5 h-5 text-red-400" />;
    message = `${bestName} sous-performe (${formatUSD(pnl, { sign: true })}, WR ${wr.toFixed(0)}%). Stratégie à revoir.`;
  }

  const toneClasses = {
    positive: 'border-emerald-500/30 bg-emerald-500/5',
    neutral: 'border-cyan-500/30 bg-cyan-500/5',
    negative: 'border-red-500/30 bg-red-500/5',
  };

  return (
    <Card className={toneClasses[tone]}>
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 mt-0.5">{icon}</div>
          <div className="flex-1">
            <div className="text-sm font-medium mb-1">Verdict</div>
            <p className="text-sm text-foreground">{message}</p>
            {tone === 'positive' && (
              <Button
                size="sm"
                variant="success"
                className="mt-3"
                onClick={() => toast.info('Création du bot en essai — à implémenter')}
              >
                <Rocket className="w-3.5 h-3.5" />
                Créer le bot (Essai)
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Backtest Results ─────────────────────────────────────────────────────

function BacktestResults({ result, scoreThreshold }: { result: any; scoreThreshold?: number | null }) {
  const r = Array.isArray(result) ? result[0] : result;
  const byStrategy = r?.by_strategy || {};
  const strategies = Object.entries(byStrategy);
  const [fsStrategy, setFsStrategy] = useState<string | null>(null);

  // S9-F4-US3/5 — Export du résultat de backtest. Les composants existaient
  // mais n'étaient montés nulle part.
  const csvRows = strategies.map(([name, stats]: [string, any]) => ({ strategy: name, ...stats }));

  // BT-007 — tableau comparatif si plusieurs stratégies. On reconstruit un
  // `BacktestResult[]` à partir de `by_strategy` (la réponse backend est
  // plate : `by_strategy[name]` porte les KPIs mais pas `strategy`/`symbol`/
  // `timeframe`, qu'on ajoute ici pour satisfaire le contrat du composant).
  const comparisonStrategies = strategies.length > 1
    ? strategies.map(([name, stats]: [string, any]) => ({
        ...(stats ?? {}),
        strategy: name,
        symbol: r?.symbol,
        timeframe: r?.timeframe,
      }))
    : [];

  const exportPdf = () => {
    // Impression / PDF navigateur (parité Jinja2 export PDF sans dépendance jspdf)
    const w = window.open('', '_blank', 'noopener,noreferrer,width=900,height=700');
    if (!w) {
      toast.error('Pop-up bloquée — autorisez les fenêtres pour l’export PDF');
      return;
    }
    const rows = strategies.map(([name, stats]: [string, any]) =>
      `<tr><td>${name}</td><td>${stats.total_trades ?? '—'}</td>`
      + `<td>${Number(stats.win_rate ?? 0).toFixed(1)}%</td>`
      + `<td>${Number(stats.total_pnl ?? 0).toFixed(2)}</td>`
      + `<td>${Number(stats.sharpe ?? 0).toFixed(2)}</td>`
      + `<td>${Number(stats.max_drawdown ?? 0).toFixed(2)}%</td></tr>`,
    ).join('');
    w.document.write(`<!DOCTYPE html><html><head><title>Backtest</title>
      <style>
        body{font-family:system-ui,sans-serif;padding:24px;color:#111}
        h1{font-size:18px} table{border-collapse:collapse;width:100%;font-size:12px}
        th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}
        th{background:#f3f4f6} .muted{color:#6b7280;font-size:11px}
      </style></head><body>
      <h1>Rapport backtest</h1>
      <p class="muted">${new Date().toLocaleString('fr-FR')} · ${r?.symbol || ''} ${r?.timeframe || ''}</p>
      <table><thead><tr><th>Stratégie</th><th>Trades</th><th>WR</th><th>PnL</th><th>Sharpe</th><th>Max DD</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="muted">Imprimer → Enregistrer au format PDF</p>
      <script>window.onload=function(){window.print()}</script>
      </body></html>`);
    w.document.close();
  };

  return (
    <div className="space-y-4">
      {/* Verdict en clair */}
      <Verdict result={result} />

      {/* QW-6 — Badge realistic_risk + diagnostics du risk gate si actif */}
      {r?.realistic_risk && (
        <Card className="border-l-4 border-l-blue-500">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <Shield className="w-4 h-4 text-blue-400" />
              Mode realistic_risk actif
              <Badge variant="info" className="text-[10px]">QW-6</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <p className="text-muted-foreground">
              Les circuit breakers (consecutive losses, slot daily DD, max
              trades/day, volatility brake, kill-switch global) ont été
              appliqués pendant le backtest. Les refus sont comptés dans les
              diagnostics ci-dessous.
            </p>
            {r?.realistic_risk_diagnostics && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
                <div className="p-2 rounded-md bg-card-hover/30">
                  <div className="text-[10px] text-dim uppercase">HALT global</div>
                  <div className={cn('font-mono font-semibold', r.realistic_risk_diagnostics.halted ? 'text-red-400' : 'text-emerald-400')}>
                    {r.realistic_risk_diagnostics.halted ? 'OUI' : 'Non'}
                  </div>
                </div>
                <div className="p-2 rounded-md bg-card-hover/30">
                  <div className="text-[10px] text-dim uppercase">Slots pausés</div>
                  <div className="font-mono font-semibold text-amber-400">
                    {r.realistic_risk_diagnostics.n_slots_paused ?? 0}
                  </div>
                </div>
                <div className="p-2 rounded-md bg-card-hover/30">
                  <div className="text-[10px] text-dim uppercase">Vol brake</div>
                  <div className={cn('font-mono font-semibold', r.realistic_risk_diagnostics.volatility_brake_active ? 'text-amber-400' : 'text-emerald-400')}>
                    {r.realistic_risk_diagnostics.volatility_brake_active ? 'ACTIF' : 'Inactif'}
                  </div>
                </div>
                <div className="p-2 rounded-md bg-card-hover/30">
                  <div className="text-[10px] text-dim uppercase">Slots monitorés</div>
                  <div className="font-mono font-semibold">
                    {Object.keys(r.realistic_risk_diagnostics.slots ?? {}).length}
                  </div>
                </div>
              </div>
            )}
            {r?.realistic_risk_diagnostics?.halted && (
              <div className="p-2 rounded-md bg-red-500/10 border border-red-500/30 mt-2">
                <span className="text-red-400 font-semibold text-xs">⚠ HALT :</span>{' '}
                <span className="text-red-300 text-xs">{r.realistic_risk_diagnostics.halt_reason}</span>
              </div>
            )}
            {r?.realistic_risk_diagnostics?.slots && Object.entries(r.realistic_risk_diagnostics.slots as Record<string, any>)
              .filter(([, s]: [string, any]) => s.paused)
              .map(([slotKey, s]: [string, any]) => (
                <div key={slotKey} className="p-2 rounded-md bg-amber-500/10 border border-amber-500/30">
                  <span className="text-amber-400 font-semibold text-xs">⏸ Slot pausé :</span>{' '}
                  <span className="text-amber-300 text-xs font-mono">{slotKey}</span>{' '}
                  <span className="text-amber-300/80 text-xs">— {s.pause_reason}</span>
                </div>
              ))}
          </CardContent>
        </Card>
      )}

      {/* Contexte facturé : sans lui, un PnL n'est pas interprétable (spot ou
          margin ? quel levier ? quels frais ?) et deux runs ne sont pas
          comparables. Cf. app/core/execution.py::cost_model. */}
      <CostModelCard model={r?.cost_model} />

      {/* QW-4 — Simulateur de coûts : compare 3 presets (spot, margin×3,
          margin×10) en relançant des backtests avec cost_override. */}
      <CostSimulatorPanel
        symbol={r?.symbol ?? ''}
        timeframe={r?.timeframe ?? ''}
        strategies={strategies.map(([n]: [string, any]) => n).join(',')}
        limit={r?.limit ?? 500}
        currentCostModel={r?.cost_model}
      />

      {/* QW-5 — Recommandations d'amélioration post-backtest (12 règles :
          échantillon, PnL, outliers, frais, Sharpe, DD, alpha, win-rate,
          borrow, régimes, points forts). Affichées par stratégie. */}
      {Object.entries(byStrategy).map(([name, stats]: [string, any]) => (
        <RecommendationsPanel
          key={`reco-${name}`}
          strategy={name}
          recommendations={stats?.recommendations}
          summary={stats?.recommendations_summary}
        />
      ))}

      {/* BT-011 — avertissements globaux : seuil recommandé et taille
          d'échantillon. Affichés au-dessus des KPIs pour être vus avant tout. */}
      {strategies.length > 0 && (() => {
        // `strategy` porte le nom quand le warning est actionnable dans Config
        // (seuil ajustable) ; null quand il ne l'est pas (taille d'échantillon,
        // qui se corrige en allongeant la période, pas dans les réglages).
        const warnings: Array<{ tone: 'amber' | 'red'; text: string; strategy: string | null }> = [];
        for (const [name, stats] of strategies) {
          const rec = recommendedThreshold(name);
          const cfgThreshold = typeof scoreThreshold === 'number' ? scoreThreshold : r?.score_threshold;
          if (rec != null && cfgThreshold != null && cfgThreshold < rec - 0.01) {
            warnings.push({
              tone: 'amber',
              text: `⚠ ${name} : score_threshold actuel (${cfgThreshold.toFixed(2)}) est inférieur au seuil recommandé (${rec.toFixed(2)}). Risque de sur-trade.`,
              strategy: name,
            });
          }
          const n = (stats as any)?.total_trades ?? 0;
          if (n > 0 && n < 30) {
            warnings.push({
              tone: 'amber',
              text: `⚠ ${name} : seulement ${n} trades — échantillon insuffisant (< 30) pour valider l'edge. Sharpe et win rate ne sont pas significatifs.`,
              strategy: null,
            });
          }
        }
        if (warnings.length === 0) return null;
        return (
          <div className="space-y-2">
            {warnings.map((w, i) => (
              <div
                key={i}
                className={
                  w.tone === 'red'
                    ? 'rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-300'
                    : 'rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300'
                }
              >
                <span>{w.text}</span>
                {/* BT-011 — rendre le warning actionnable.
                    La spec demandait un lien vers `/settings?tab=strategies&
                    strategy=<nom>` : cet onglet N'EXISTE PAS. Le front n'a
                    aucun éditeur de `strategy_params` — `api.updateStrategyParams`
                    est défini dans `lib/api.ts` mais aucun composant ne l'appelle,
                    et l'éditeur Jinja2 (`config.html`) n'a jamais été reporté.
                    Pointer vers cette route donnerait un lien mort. On envoie donc
                    vers l'optimiseur, seul chemin en place pour recalculer puis
                    appliquer les paramètres d'une stratégie. */}
                {w.strategy && (
                  <Link
                    href={`/lab?tab=optimizer&strategy=${encodeURIComponent(w.strategy)}`}
                    className="ml-2 underline underline-offset-2 font-medium hover:no-underline whitespace-nowrap"
                    title={`Optimiser ${w.strategy} puis appliquer les paramètres`}
                  >
                    Optimiser cette stratégie →
                  </Link>
                )}
              </div>
            ))}
          </div>
        );
      })()}

      <div className="flex items-center justify-end gap-2 flex-wrap">
        <CsvExportButton
          filename="backtest"
          rows={csvRows}
          headers={{
            strategy: 'Stratégie',
            total_trades: 'Trades',
            win_rate: 'Win rate',
            total_pnl: 'PnL',
            sharpe: 'Sharpe',
            max_drawdown: 'Max DD',
          }}
        />
        <JsonExportButton filename="backtest" data={result} />
        <Button size="sm" variant="outline" onClick={exportPdf}>
          <FileDown className="w-3.5 h-3.5" />
          PDF
        </Button>
      </div>

      {/* BT-007 — tableau comparatif multi-stratégies (best value ✦).
          Nécessite ≥ 2 stratégies ; sinon le composant renvoie null. */}
      {comparisonStrategies.length >= 2 && (
        <StrategyComparisonTable strategies={comparisonStrategies} />
      )}

      {/* Fullscreen chart modal (complément ChartFullscreen déjà dans le composant) */}
      {fsStrategy && byStrategy[fsStrategy] && (
        <div className="fixed inset-0 z-50 bg-black/80 flex flex-col p-4" role="dialog" aria-modal="true" aria-label="Chart plein écran">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-mono font-semibold text-white">{fsStrategy} — plein écran</h3>
            <Button size="sm" variant="ghost" onClick={() => setFsStrategy(null)} aria-label="Fermer">
              <X className="w-4 h-4" /> Fermer
            </Button>
          </div>
          <div className="flex-1 min-h-0 bg-card rounded-lg border border-border p-2 overflow-auto">
            <BacktestEquityChart
              strategy={fsStrategy}
              equityCurve={byStrategy[fsStrategy]?.equity_curve}
              initialCapital={byStrategy[fsStrategy]?.initial_capital}
              buyAndHoldPnl={byStrategy[fsStrategy]?.buy_and_hold_pnl}
              alpha={byStrategy[fsStrategy]?.alpha}
            />
          </div>
        </div>
      )}

      {/* BT-006 — KPIs par stratégie. 9 métriques au lieu de 5 : PnL Net (+%),
          Win Rate (+n trades), Sharpe (⚠ si < 30 trades), Max DD, Expectancy,
          Profit Factor, Equity Finale, Buy & Hold (+%), Alpha. */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {strategies.map(([name, stats]: [string, any]) => {
          const nTrades = stats?.total_trades ?? 0;
          const smallSample = nTrades < 30;
          const eqFinal = equityFinal({ ...(stats ?? {}), strategy: name });
          const bh = buyHold({ ...(stats ?? {}), strategy: name });
          const alpha = stats?.alpha;
          const bhPct = bh.pct;
          const pnlPct = stats?.total_pnl && stats?.initial_capital
            ? (stats.total_pnl / stats.initial_capital) * 100
            : null;
          return (
            <Card key={name}>
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="font-mono text-sm font-semibold">{name}</span>
                  <Badge variant={stats.total_pnl >= 0 ? 'success' : 'danger'}>
                    {stats.total_pnl >= 0 ? '+' : ''}{formatUSD(stats.total_pnl)}
                    {pnlPct != null && (
                      <span className="ml-1 opacity-70">
                        ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
                      </span>
                    )}
                  </Badge>
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <div className="text-dim">Trades</div>
                    <div className="font-mono font-semibold">
                      {nTrades}
                      {stats?.win_rate != null && (
                        <span className="text-dim ml-1">({Math.round((stats.win_rate / 100) * nTrades)}w)</span>
                      )}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Win Rate</div>
                    <div className={cn('font-mono font-semibold', stats.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400')}>
                      {stats.win_rate?.toFixed(1) ?? '—'}%
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Sharpe{smallSample ? ' ⚠' : ''}</div>
                    <div
                      className={cn(
                        'font-mono font-semibold',
                        smallSample ? 'text-amber-400' : '',
                      )}
                      title={smallSample ? '< 30 trades — Sharpe non significatif' : undefined}
                    >
                      {stats.sharpe?.toFixed(2) ?? '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Max DD</div>
                    <div className="font-mono font-semibold text-red-400">
                      {stats.max_drawdown?.toFixed(2) ?? '—'}%
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Expectancy</div>
                    <div className={cn('font-mono font-semibold', (stats.expectancy ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                      {stats.expectancy != null
                        ? `${stats.expectancy >= 0 ? '+' : ''}${formatUSD(stats.expectancy)}`
                        : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">PF</div>
                    <div className="font-mono font-semibold">
                      {stats.profit_factor === 999 ? '∞' : stats.profit_factor?.toFixed(2) ?? '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Equity Finale</div>
                    <div className="font-mono font-semibold">
                      {eqFinal != null ? formatUSD(eqFinal) : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Buy &amp; Hold</div>
                    <div className={cn('font-mono font-semibold', (bh.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                      {bh.pnl != null
                        ? `${bh.pnl >= 0 ? '+' : ''}${formatUSD(bh.pnl)}`
                        : '—'}
                      {bhPct != null && (
                        <span className="text-dim ml-1">
                          ({bhPct >= 0 ? '+' : ''}{bhPct.toFixed(1)}%)
                        </span>
                      )}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Alpha</div>
                    <div className={cn('font-mono font-semibold', (alpha ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                      {alpha != null ? `${alpha >= 0 ? '+' : ''}${alpha.toFixed(2)}%` : '—'}
                    </div>
                  </div>
                </div>

              </CardContent>
            </Card>
          );
        })}
      </div>

      {/*
        Détail par stratégie : courbe d'équité (+ Buy & Hold), Walk-Forward,
        Monte-Carlo et scatter des trades.

        Ces blocs remplacent deux lignes de résumé qui lisaient des champs
        inexistants (`walk_forward.folds`, `walk_forward.oos_pnl`,
        `monte_carlo.p5/.p50/.p95`) et affichaient donc « 0 folds » et « P5:
        $0.00 · P50: $0.00 · P95: $0.00 » quel que soit le résultat. Les
        composants ci-dessous sont écrits sur les contrats réels du backend.

        Le Walk-Forward et le Monte-Carlo ne sont plus derrière `expertMode` :
        ils ne sont calculés QUE si l'utilisateur a explicitement coché la case
        correspondante avant de lancer. Les masquer une seconde fois revenait à
        cacher un résultat demandé.
      */}
      {strategies.map(([name, stats]: [string, any]) => {
        const trades = Array.isArray(stats?.trades) ? stats.trades : [];
        const hasDetail = stats?.equity_curve?.length || stats?.walk_forward
          || stats?.monte_carlo || stats?.runs || trades.length > 0;
        if (!hasDetail) return null;
        // BT-001 — `candles` vient de la racine (r.ohlcv), partagées par toutes
        // les stratégies ; `trades` est filtré par stratégie via le loop.
        const candles = r?.ohlcv;
        const isMl = String(name).startsWith('ml_');
        return (
          <div key={`detail-${name}`} className="space-y-4">
            <div className="flex items-center justify-between pt-2">
              <h3 className="text-xs uppercase tracking-wide text-dim">{name}</h3>
              <Button size="sm" variant="ghost" onClick={() => setFsStrategy(name)} title="Plein écran">
                <Maximize2 className="w-3.5 h-3.5" />
                Plein écran
              </Button>
            </div>

            {/* BT-001 — chart prix + signaux (markers entrée/sortie + stops). */}
            {process.env.NEXT_PUBLIC_LAB_PRICE_CHART !== 'false' && candles && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Prix &amp; signaux — {name}</CardTitle>
                </CardHeader>
                <CardContent>
                  <PriceSignalsChart candles={candles} trades={trades} />
                </CardContent>
              </Card>
            )}

            <BacktestEquityChart
              strategy={name}
              equityCurve={stats.equity_curve}
              initialCapital={stats.initial_capital}
              buyAndHoldPnl={stats.buy_and_hold_pnl}
              alpha={stats.alpha}
            />
            {stats.runs && (
              <StudyVsLiveCard runs={stats.runs} envelope={stats.envelope} />
            )}
            {stats.walk_forward && <WalkForwardTable data={stats.walk_forward} />}

            {/* BT-002 — tableau des trades (sortable, paginé, expandable, CSV). */}
            {trades.length > 0 && (
              <TradesTable
                trades={trades}
                meta={{
                  symbol: r?.symbol ?? '',
                  timeframe: r?.timeframe ?? '',
                  strategy: name,
                }}
              />
            )}

            {/* BT-008 — statistiques agrégées des trades (par setup, par sortie). */}
            {trades.length > 0 && (
              <TradesStatsPanel trades={trades} />
            )}

            {/* BT-003 — diagnostics de recherche de signaux (rejets, per-strat). */}
            <DiagnosticsPanel diagnostics={normalizeDiagnostics(stats?.diagnostics ?? r?.diagnostics)} />

            {/* BT-010 — panneau ML (AUC, n_features) pour les stratégies `ml_*`. */}
            {isMl && (
              <MLBacktestPanel
                mlInfo={stats?.ml_info}
                strategy={name}
                nTrades={stats?.total_trades ?? 0}
              />
            )}

            {stats.monte_carlo && (
              <MonteCarloPanel
                data={stats.monte_carlo}
                initialCapital={stats.initial_capital}
                nTrades={stats.total_trades}
              />
            )}
            <TradesScatter trades={trades} symbol={r?.symbol} />
          </div>
        );
      })}

      {/* Equity curve (si disponible) */}
      {r?.ohlcv && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Prix OHLCV ({r.symbol} · {r.timeframe})</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted">
              {r.n_bars} bougies · {r.date_from} → {r.date_to}
              {/* QW-2 — rappeler la plage DEMANDÉE à côté de la plage OBTENUE.
                  Les deux diffèrent dès que le cache est plus court que la
                  demande ; sans ce rappel, l'écart passe inaperçu. */}
              {(r.requested_start_date || r.requested_end_date) && (
                <span className="text-dim ml-2">
                  (demandé : {r.requested_start_date || '…'} → {r.requested_end_date || '…'})
                </span>
              )}
              {r.gaps_warning && (
                <span className="text-amber-400 ml-2">⚠ {r.gaps_warning}</span>
              )}
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ── ML Tab ───────────────────────────────────────────────────────────────
//
// Seul onglet du Lab qui garde un renvoi : `/models` (registre versionné,
// gate de promotion, 882 l.) n'est PAS dans le plan de fusion et reste une
// page à part entière. Le renvoi est donc assumé, pas un reste de teaser —
// et `/models` n'a par conséquent aucune 308.

function MLTab() {
  return (
    <div className="space-y-4">
      <MLView />
      <Card>
        <CardContent className="p-4 flex items-center justify-between gap-4 flex-wrap">
          <p className="text-sm text-muted">
            Le registre versionné (gate de promotion, pin, sweep) reste une page dédiée.
          </p>
          {/* `Button` ne gère pas `asChild` : on style le Link directement
              plutôt que d'imbriquer un <a> dans un <button> (HTML invalide,
              et axe le remonte en `nested-interactive`). */}
          <Link
            href="/models"
            className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-border text-sm text-muted hover:text-foreground hover:bg-card-hover transition-colors"
          >
            <Archive className="w-3.5 h-3.5" />
            Registre modèles
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
