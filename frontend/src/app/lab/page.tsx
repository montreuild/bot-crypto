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
 */

import { useState, useEffect, Suspense } from 'react';
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
import { MonteCarloPanel } from '@/components/charts/monte-carlo-panel';
import { TradesScatter } from '@/components/charts/trades-scatter';
import { BacktestEquityChart } from '@/components/charts/backtest-equity-chart';
import { useBacktestSettings, useRunBacktest, useCancelBacktest } from '@/hooks/use-api';
import { useConfig, usePresets, useSetExpertMode } from '@/hooks/use-api';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';
import {
  Play, Square, Loader2, FlaskConical, Sparkles, Brain, Repeat,
  GitCompare, AlertCircle, CheckCircle2, TrendingUp, Rocket,
} from 'lucide-react';
import { cn, formatUSD, formatPct } from '@/lib/utils';

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
  const initialTab = searchParams.get('tab') || 'backtest';
  const intent = searchParams.get('intent'); // 'create' si arrivé depuis /bots-v2
  const [tab, setTab] = useState(initialTab);

  // Mode expert — source de vérité = le backend (`/api/settings/presets`), comme
  // sur /settings-v2. Cette page lisait auparavant uniquement `localStorage`, via
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
        <TabsList className="grid grid-cols-5 w-full max-w-2xl">
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
          <TabsTrigger value="compare">
            <GitCompare className="w-3.5 h-3.5 mr-1.5" />
            Compare
          </TabsTrigger>
        </TabsList>

        <TabsContent value="backtest">
          <BacktestTab expertMode={expertMode} />
        </TabsContent>
        <TabsContent value="optimizer">
          <OptimizerTab expertMode={expertMode} />
        </TabsContent>
        <TabsContent value="ml">
          <MLTab expertMode={expertMode} />
        </TabsContent>
        <TabsContent value="replay">
          <ReplayTab expertMode={expertMode} />
        </TabsContent>
        <TabsContent value="compare">
          <CompareTab expertMode={expertMode} />
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
  walk_forward: boolean;
  monte_carlo: boolean;
  strategies: string[];
}

function BacktestTab({ expertMode }: { expertMode: boolean }) {
  const settingsQuery = useBacktestSettings();
  const runBacktest = useRunBacktest();
  const cancelBacktest = useCancelBacktest();
  const qc = useQueryClient();

  const { data: settings } = settingsQuery;

  const [config, setConfig] = useState<BacktestConfig>({
    symbol: 'BTC/USDC',
    timeframe: '1h',
    limit: 500,
    walk_forward: false,
    monte_carlo: false,
    strategies: [],
  });
  const [result, setResult] = useState<any>(null);

  const handleRun = async () => {
    setResult(null);
    try {
      const res = await runBacktest.mutateAsync({
        symbol: config.symbol,
        timeframe: config.timeframe,
        limit: config.limit,
        walk_forward: config.walk_forward,
        monte_carlo: config.monte_carlo,
        strategies: config.strategies.length > 0 ? config.strategies.join(',') : undefined,
      });
      setResult(res);
      toast.success('Backtest terminé');
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  const handleCancel = async () => {
    try {
      await cancelBacktest.mutateAsync();
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

  const availableStrategies = settings?.strategies || [];
  const availableTfs = settings?.available_timeframes || ['5m', '15m', '30m', '1h', '4h', '1d'];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
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
              onChange={(e) => setConfig({ ...config, symbol: e.target.value })}
              placeholder="BTC/USDC"
              className="font-mono"
            />
          </div>

          <div>
            <Label htmlFor="bt-tf">Timeframe</Label>
            <Select value={config.timeframe} onValueChange={(v) => setConfig({ ...config, timeframe: v })}>
              <SelectTrigger id="bt-tf">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {availableTfs.map((tf: string) => (
                  <SelectItem key={tf} value={tf}>{tf}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

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
            <p className="text-[10px] text-dim mt-1">
              ≈ {Math.round(config.limit * (config.timeframe === '1h' ? 1 : 1))}h de données
            </p>
          </div>

          {/* Stratégies */}
          <div>
            <Label>Stratégies ({config.strategies.length} sélectionnées)</Label>
            <div className="max-h-32 overflow-y-auto space-y-1 mt-1 border border-border rounded-md p-2">
              {availableStrategies.length === 0 ? (
                <p className="text-xs text-dim">Chargement…</p>
              ) : (
                availableStrategies.map((s: string) => (
                  <label key={s} className="flex items-center gap-2 text-xs cursor-pointer hover:bg-card-hover rounded px-1 py-0.5">
                    <input
                      type="checkbox"
                      checked={config.strategies.includes(s)}
                      onChange={() => toggleStrategy(s)}
                    />
                    <span className="font-mono">{s}</span>
                  </label>
                ))
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
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <Button
              onClick={handleRun}
              disabled={runBacktest.isPending}
              className="flex-1"
            >
              {runBacktest.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              Analyser
            </Button>
            {runBacktest.isPending && (
              <Button variant="danger" onClick={handleCancel}>
                <Square className="w-4 h-4" />
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Results panel */}
      <div className="lg:col-span-2 space-y-4">
        {!result && !runBacktest.isPending && (
          <Card>
            <CardContent className="flex items-center justify-center min-h-[300px] text-muted text-sm">
              Configure et lance un backtest pour voir les résultats
            </CardContent>
          </Card>
        )}

        {runBacktest.isPending && (
          <Card>
            <CardContent className="flex items-center justify-center min-h-[300px]">
              <Loader2 className="w-8 h-8 animate-spin text-primary-400" />
              <span className="ml-3 text-sm text-muted">Backtest en cours…</span>
            </CardContent>
          </Card>
        )}

        {result && <BacktestResults result={result} />}
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

function BacktestResults({ result }: { result: any }) {
  const r = Array.isArray(result) ? result[0] : result;
  const byStrategy = r?.by_strategy || {};
  const strategies = Object.entries(byStrategy);

  // S9-F4-US3/5 — Export du résultat de backtest. Les composants existaient
  // mais n'étaient montés nulle part.
  const csvRows = strategies.map(([name, stats]: [string, any]) => ({ strategy: name, ...stats }));

  return (
    <div className="space-y-4">
      {/* Verdict en clair */}
      <Verdict result={result} />

      <div className="flex items-center justify-end gap-2">
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
      </div>

      {/* KPIs par stratégie */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {strategies.map(([name, stats]: [string, any]) => (
          <Card key={name}>
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="font-mono text-sm font-semibold">{name}</span>
                <Badge variant={stats.total_pnl >= 0 ? 'success' : 'danger'}>
                  {stats.total_pnl >= 0 ? '+' : ''}{formatUSD(stats.total_pnl)}
                </Badge>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-dim">Trades</div>
                  <div className="font-mono font-semibold">{stats.total_trades}</div>
                </div>
                <div>
                  <div className="text-dim">Win Rate</div>
                  <div className={cn('font-mono font-semibold', stats.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400')}>
                    {stats.win_rate.toFixed(1)}%
                  </div>
                </div>
                <div>
                  <div className="text-dim">Sharpe</div>
                  <div className="font-mono font-semibold">{stats.sharpe?.toFixed(2) ?? '—'}</div>
                </div>
                <div>
                  <div className="text-dim">Max DD</div>
                  <div className="font-mono font-semibold text-red-400">{stats.max_drawdown?.toFixed(2) ?? '—'}%</div>
                </div>
                <div>
                  <div className="text-dim">PF</div>
                  <div className="font-mono font-semibold">
                    {stats.profit_factor === 999 ? '∞' : stats.profit_factor?.toFixed(2) ?? '—'}
                  </div>
                </div>
              </div>

            </CardContent>
          </Card>
        ))}
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
          || stats?.monte_carlo || trades.length > 0;
        if (!hasDetail) return null;
        return (
          <div key={`detail-${name}`} className="space-y-4">
            <h3 className="text-xs uppercase tracking-wide text-dim pt-2">{name}</h3>
            <BacktestEquityChart
              strategy={name}
              equityCurve={stats.equity_curve}
              initialCapital={stats.initial_capital}
              buyAndHoldPnl={stats.buy_and_hold_pnl}
              alpha={stats.alpha}
            />
            {stats.walk_forward && <WalkForwardTable data={stats.walk_forward} />}
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

// ── Optimizer Tab (placeholder inline pour simplifier) ───────────────────

function OptimizerTab({ expertMode }: { expertMode: boolean }) {
  return (
    <Card>
      <CardContent className="p-8 text-center">
        <Sparkles className="w-10 h-10 mx-auto text-primary-400 mb-3" />
        <h3 className="text-base font-semibold mb-1">Optimiseur bayésien</h3>
        <p className="text-sm text-muted max-w-md mx-auto mb-4">
          Optimise les paramètres de tes stratégies avec méthodes Bayesian/Random/Grid.
          L&apos;optimizer existant reste accessible via le menu latéral « Optimiseur ».
        </p>
        <Button variant="outline" onClick={() => window.location.href = '/optimizer'}>
          Aller à l&apos;optimiseur existant
        </Button>
        {expertMode && (
          <p className="text-xs text-dim mt-4">
            Mode expert : intégration native dans le Laboratoire prévue au Sprint 7
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function MLTab({ expertMode }: { expertMode: boolean }) {
  return (
    <Card>
      <CardContent className="p-8 text-center">
        <Brain className="w-10 h-10 mx-auto text-purple-400 mb-3" />
        <h3 className="text-base font-semibold mb-1">Entraînement ML</h3>
        <p className="text-sm text-muted max-w-md mx-auto mb-4">
          Entraîne des modèles LightGBM avec recettes déclaratives, window sweep et
          gate de promotion. Le registre ML reste accessible via « Modèles ML ».
        </p>
        <div className="flex justify-center gap-2">
          <Button variant="outline" onClick={() => window.location.href = '/ml'}>
            Modèles ML
          </Button>
          <Button variant="outline" onClick={() => window.location.href = '/models'}>
            Registre
          </Button>
        </div>
        {expertMode && (
          <p className="text-xs text-dim mt-4">
            Mode expert : intégration native prévue au Sprint 9
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ReplayTab({ expertMode }: { expertMode: boolean }) {
  return (
    <Card>
      <CardContent className="p-8 text-center">
        <Repeat className="w-10 h-10 mx-auto text-cyan-400 mb-3" />
        <h3 className="text-base font-semibold mb-1">Replay multi-TF</h3>
        <p className="text-sm text-muted max-w-md mx-auto mb-4">
          Rejoue plusieurs timeframes en parallèle sur une période donnée.
          Le replay existant reste accessible via le menu latéral.
        </p>
        <Button variant="outline" onClick={() => window.location.href = '/replay'}>
          Aller au replay existant
        </Button>
      </CardContent>
    </Card>
  );
}

function CompareTab({ expertMode }: { expertMode: boolean }) {
  return (
    <Card>
      <CardContent className="p-8 text-center">
        <GitCompare className="w-10 h-10 mx-auto text-emerald-400 mb-3" />
        <h3 className="text-base font-semibold mb-1">Comparatif multi-stratégies</h3>
        <p className="text-sm text-muted max-w-md mx-auto mb-4">
          Compare jusqu&apos;à 12 stratégies sur une même fenêtre temporelle.
          Le comparatif existant reste accessible via le menu latéral.
        </p>
        <Button variant="outline" onClick={() => window.location.href = '/compare'}>
          Aller au comparatif existant
        </Button>
      </CardContent>
    </Card>
  );
}
