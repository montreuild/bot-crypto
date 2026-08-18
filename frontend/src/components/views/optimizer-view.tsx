'use client';

/**
 * Onglet Optimizer de `/lab` — optimisation bayésienne / grid / random.
 *
 * Lot Laboratoire : cette vue était la page `/optimizer`, que l'onglet se
 * contentait de teaser (« Aller à l'optimiseur existant », « intégration
 * native prévue au Sprint 7 »). `/optimizer` est désormais en 308 vers
 * `/lab?tab=optimizer`.
 */

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD } from '@/lib/utils';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import {
  useOptimizeSpaces,
  useOptimizeResults,
  useOptimizeStatus,
  useStartOptimize,
  useConfig,
} from '@/hooks/use-api';
import {
  Play, Loader2, CheckCircle2, XCircle,
  Sparkles, Cpu, Layers, Activity, Zap, ChevronDown, ChevronRight, Info,
  History, FileDown, GitCompare,
} from 'lucide-react';
import type { OptimizeJob } from '@/types';
import { OptimizerHistory } from '@/components/cards/optimizer-history';
import { ParamSpaceTable } from '@/components/optimizer/param-space-table';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { isOosHint } from '@/lib/limit-hint';
import { JobCard } from '@/components/optimizer/job-card';
import {
  METHODS, FALLBACK_SYMBOLS, PRESETS, STATUS_LABEL,
  type PresetKey,
} from '@/components/optimizer/status';

export function OptimizerView({ filterMl = false }: { filterMl?: boolean }) {
  const { data: spaces, isLoading: spacesLoading } = useOptimizeSpaces();
  const { data: resultsData } = useOptimizeResults();
  const startOptimize = useStartOptimize();
  const { timeframes: activeTfs, defaultTf } = useTradingTimeframes('1h');

  // All known strategies from param space (fallback to defaults).
  // Mémoïsé : ce tableau alimente les deps d'un useEffect plus bas ; recréé à
  // chaque render, il relançait l'effet en boucle.
  const allStrategies = useMemo(() => (spaces ? Object.keys(spaces) : []), [spaces]);

  // ML-001 — en mode ML, on ne propose QUE les stratégies marquées `is_ml`
  // par le backend (`/optimize/spaces`). On garde une passe défensive sur
  // `is_ml` (booléen attendu, mais on tolère une string "true"/falsy).
  const visibleStrategies = useMemo(() => {
    if (!filterMl) return allStrategies;
    return allStrategies.filter((s) => {
      const info = spaces?.[s];
      return !!(info?.is_ml);
    });
  }, [allStrategies, filterMl, spaces]);

  // BT-011 — cible éventuelle passée en query (`?strategy=<nom>`).
  const searchParams = useSearchParams();

  // Form state
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [selectedTfs, setSelectedTfs] = useState<string[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(['BTC/USDC']);
  const [method, setMethod] = useState<(typeof METHODS)[number]>('bayesian');
  const [nTrials, setNTrials] = useState(60);
  const [nJobs, setNJobs] = useState(1);
  const [autoApply, setAutoApply] = useState(false);
  const [paramSearchOptim, setParamSearchOptim] = useState(true);
  // P1-2 : preset actif (null = custom). Changer de preset set nTrials/nJobs/etc.
  const [activePreset, setActivePreset] = useState<PresetKey | null>(null);
  // OPT-004 — trois nouvelles options d'optimisation. `early_stopping` et
  // `limit_per_tf` sont des inputs numériques ; `ml_tune_hp` n'est visible
  // que si une stratégie ML est sélectionnée.
  const [earlyStopping, setEarlyStopping] = useState(0);
  const [limitPerTf, setLimitPerTf] = useState(0);
  const [mlTuneHp, setMlTuneHp] = useState(false);

  // P1-7 : symbols dynamiques depuis la config. Lit `useConfig()` qui tape
  // sur /api/config et expose scanner.symbols. Fallback sur FALLBACK_SYMBOLS.
  const { data: configData } = useConfig();
  const availableSymbols = useMemo(() => {
    const scannerSymbols = (configData as { scanner?: { symbols?: string[] } } | undefined)?.scanner?.symbols;
    if (Array.isArray(scannerSymbols) && scannerSymbols.length > 0) {
      return scannerSymbols;
    }
    return FALLBACK_SYMBOLS;
  }, [configData]);

  // P1-2 : appliquer un preset set les valeurs et marque le preset actif.
  // Modifier manuellement un champ set activePreset=null (custom).
  function applyPreset(key: PresetKey) {
    const p = PRESETS[key];
    setNTrials(p.nTrials);
    setNJobs(p.nJobs);
    setEarlyStopping(p.earlyStopping);
    setMlTuneHp(p.mlTuneHp);
    setActivePreset(key);
  }
  function markCustom() {
    if (activePreset !== null) setActivePreset(null);
  }

  // OPT-007 — état global "tout déplié / tout replié". Bascule l'état par
  // défaut passé aux JobCards ; le toggle lui-même est au-dessus de la grille.
  const [allExpanded, setAllExpanded] = useState(false);

  // OPT-008 — feedback immédiat après « Lancer l'optimisation » :
  // carte ambre "Récupération des bougies en cours…" pendant l'appel, puis
  // carte verte "✓ N job(s) lancé(s)" avec le détail des bougies reçues par
  // TF et les combinaisons ignorées.
  type LaunchFeedback =
    | { kind: 'fetching' }
    | { kind: 'ok'; nJobs: number; receivedBars?: Record<string, number>; fetchDetails?: Record<string, number>; skipped?: unknown[] }
    | null;
  const [launchFeedback, setLaunchFeedback] = useState<LaunchFeedback>(null);

  // Jobs list — polled via the useOptimizeStatus hook (no jobId = all jobs)
  const {
    data: jobsData,
    isLoading: jobsLoading,
    isError: jobsIsError,
    error: jobsErrorObj,
  } = useOptimizeStatus();

  const jobs: OptimizeJob[] = (() => {
    const d = jobsData;
    if (!d) return [];
    if (Array.isArray(d)) return d as OptimizeJob[];
    if (typeof d !== 'object') return [];
    const rec = d as Record<string, unknown>;
    if (Array.isArray(rec.jobs)) return rec.jobs as OptimizeJob[];
    if (typeof rec.job_id === 'string') return [d as OptimizeJob];
    return Object.entries(rec).map(([job_id, job]) => ({
      job_id,
      ...(job && typeof job === 'object' ? job : {}),
    })) as OptimizeJob[];
  })();
  const jobsError = jobsIsError
    ? (jobsErrorObj instanceof Error ? jobsErrorObj.message : 'Erreur de chargement')
    : null;

  // P1-6 : filtre/recherche sur la liste de jobs
  const [jobsSearch, setJobsSearch] = useState('');
  const [jobsStatusFilter, setJobsStatusFilter] = useState('');
  // P1-10 : mode comparaison de jobs (max 4)
  const [compareMode, setCompareMode] = useState(false);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const toggleCompare = (jobId: string) => {
    setCompareIds((prev) =>
      prev.includes(jobId)
        ? prev.filter((id) => id !== jobId)
        : prev.length < 4 ? [...prev, jobId] : prev,
    );
  };
  const filteredJobs = jobs.filter((j) => {
    if (jobsStatusFilter && j.status !== jobsStatusFilter) return false;
    if (jobsSearch) {
      const q = jobsSearch.toLowerCase();
      if (!j.strategy?.toLowerCase().includes(q) &&
          !j.job_id?.toLowerCase().includes(q) &&
          !j.symbol?.toLowerCase().includes(q) &&
          !j.timeframe?.toLowerCase().includes(q)) return false;
    }
    return true;
  });
  const compareJobs = filteredJobs.filter((j) => compareIds.includes(j.job_id));

  // P2-5 : export CSV des jobs
  const exportJobsCsv = () => {
    const headers = ['job_id', 'strategy', 'timeframe', 'symbol', 'status', 'progress', 'best_score', 'trials_done', 'n_trials', 'method', 'applied'];
    const rows = filteredJobs.map((j) => [
      j.job_id, j.strategy, j.timeframe, j.symbol ?? '', j.status,
      j.progress, j.best_score, j.trials_done, j.n_trials, j.method, j.applied ? 'oui' : 'non',
    ]);
    const csv = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `optimizer-jobs-${new Date().toISOString().slice(0, 19)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Sync default strategies selection once spaces load.
  // BT-011 : `?strategy=<nom>` présélectionne la stratégie visée — sans quoi
  // le lien « Optimiser cette stratégie » des avertissements du backtest
  // atterrissait ici sur la première stratégie de la liste, pas sur celle qui
  // a déclenché l'avertissement.
  useEffect(() => {
    if (visibleStrategies.length === 0 || selectedStrategies.length > 0) return;
    const wanted = searchParams.get('strategy');
    if (wanted && visibleStrategies.includes(wanted)) {
      setSelectedStrategies([wanted]);
    } else {
      setSelectedStrategies(visibleStrategies.slice(0, 1));
    }
  }, [visibleStrategies, selectedStrategies.length, searchParams]);

  // TF actifs (config) par défaut
  useEffect(() => {
    if (activeTfs.length > 0 && selectedTfs.length === 0) {
      setSelectedTfs(activeTfs.includes(defaultTf) ? [defaultTf] : [activeTfs[0]]);
    }
  }, [activeTfs, defaultTf, selectedTfs.length]);

  const toggle = (list: string[], value: string, setter: (v: string[]) => void) => {
    setter(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  };

  // OPT-011 — détermine si une stratégie a au moins un TF sélectionné hors de
  // sa liste recommandée (pour afficher le badge ambre ⚠ à côté du chip).
  const hasNonRecommendedTf = (s: string): boolean => {
    const info = spaces?.[s];
    if (!info) return false;
    const recTfs: string[] = info.recommended_tfs ?? info.timeframes ?? [];
    if (recTfs.length === 0) return false;
    return selectedTfs.some((tf) => !recTfs.includes(tf));
  };
  const recommendedTfsFor = (s: string): string[] => {
    const info = spaces?.[s];
    if (!info) return [];
    return info.recommended_tfs ?? info.timeframes ?? [];
  };

  // OPT-004 — ml_tune_hp n'a de sens que si une stratégie ML est sélectionnée.
  const hasMlSelected = selectedStrategies.some((s) => spaces?.[s]?.is_ml);
  // ML-006 — détection d'une recette « omnibus » (multi-têtes) pour le warning
  // bougies ≥ 2200. On fait matcher le nom en sous-chaîne : les recettes
  // connues (`opus_omnibus_v11`, `omnibus_v2`…) contiennent toutes `omnibus`.
  const hasOmnibusSelected = selectedStrategies.some((s) => /omnibus/i.test(s));

  const handleStart = async () => {
    if (selectedStrategies.length === 0) {
      toast.error('Sélectionnez au moins une stratégie');
      return;
    }
    if (selectedTfs.length === 0) {
      toast.error('Sélectionnez au moins un timeframe');
      return;
    }
    if (selectedSymbols.length === 0) {
      toast.error('Sélectionnez au moins un symbole');
      return;
    }
    setLaunchFeedback({ kind: 'fetching' });
    try {
      const res: any = await startOptimize.mutateAsync({
        strategies: selectedStrategies.join(','),
        timeframes: selectedTfs.join(','),
        symbols: selectedSymbols.join(','),
        method,
        n_trials: nTrials,
        n_jobs: nJobs,
        auto_apply: autoApply,
        param_search_optim: paramSearchOptim,
        // OPT-004 — early_stopping (alias backend `early_stop_patience`).
        early_stop_patience: earlyStopping,
        // OPT-004 — limit_per_tf : le backend n'a qu'un seul paramètre `limit`
        // appliqué à chaque TF. On lui passe donc `limit` (renommage frontend).
        limit: limitPerTf > 0 ? limitPerTf : undefined,
        // OPT-004 — ml_tune_hp : n'est envoyé que si une stratégie ML est
        // sélectionnée, pour éviter de changer le comportement des stratégies
        // classiques (le backend l'ignore si False, mais on reste explicite).
        ml_tune_hp: hasMlSelected ? mlTuneHp : undefined,
      });
      const nCreated = typeof res?.n_jobs_created === 'number'
        ? res.n_jobs_created
        : Array.isArray(res?.job_ids) ? res.job_ids.length : 0;
      setLaunchFeedback({
        kind: 'ok',
        nJobs: nCreated,
        receivedBars: res?.received_bars,
        fetchDetails: res?.fetch_details,
        skipped: res?.skipped,
      });
      toast.success(`${nCreated} job(s) lancé(s)`);
    } catch (e: any) {
      setLaunchFeedback(null);
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const activeResults = resultsData?.active_per_tf || {};

  // OPT-007 — groupes de jobs par statut. Ordre : En cours > Erreurs >
  // Annulés > Terminés (les plus actionnables d'abord).
  const jobGroups = useMemo(() => {
    const groups: Array<{ key: string; label: string; jobs: OptimizeJob[]; defaultExpanded: boolean }> = [
      { key: 'running', label: 'En cours', jobs: [], defaultExpanded: true },
      { key: 'error', label: 'Erreurs', jobs: [], defaultExpanded: true },
      { key: 'cancelled', label: 'Annulés', jobs: [], defaultExpanded: false },
      { key: 'done', label: 'Terminés', jobs: [], defaultExpanded: false },
    ];
    const map: Record<string, typeof groups[number]> = Object.fromEntries(groups.map((g) => [g.key, g]));
    for (const j of filteredJobs) {
      const g = map[j.status] ?? map.done;
      g.jobs.push(j);
    }
    return groups.filter((g) => g.jobs.length > 0);
  }, [filteredJobs]);
  // OPT-007 — état d'expansion par groupe.
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const toggleGroup = (key: string) =>
    setCollapsedGroups((s) => ({ ...s, [key]: !s[key] }));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">
            {filterMl ? 'Optimiseur ML' : 'Optimiseur'}
          </h2>
          <p className="text-sm text-muted mt-1">
            {filterMl
              ? 'Optimisation bayésienne / grid / random des stratégies ML avec validation OOS et entraînement LightGBM'
              : 'Optimisation bayésienne / grid / random des stratégies avec validation OOS'}
          </p>
        </div>
        <Badge variant="info">
          <Activity className="w-3 h-3" />
          {jobs.filter((j) => j.status === 'running').length} en cours
        </Badge>
      </div>

      {/* Configuration form */}
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
          <Sparkles className="w-4 h-4 text-primary-400" />
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Strategies */}
          <div>
            <div className="text-xs text-dim mb-2 flex items-center gap-2">
              <Layers className="w-3 h-3" /> {filterMl ? 'Stratégies ML' : 'Stratégies'}
            </div>
            <div className="flex flex-wrap gap-2">
              {/* ML-001 — en mode ML, on ne propose QUE les stratégies ML
                  chargées depuis `/optimize/spaces`. Le fallback legacy
                  (`pullback_trend`…) ne contient aucune stratégie ML : l'afficher
                  en mode ML tromperait l'utilisateur avec des chips qui ne
                  peuvent pas lancer d'optimisation ML. */}
              {(filterMl
                ? visibleStrategies
                : (allStrategies.length > 0
                    ? allStrategies
                    : ['pullback_trend', 'trend_rider', 'breakout', 'smart_money'])
              ).map((s) => {
                const active = selectedStrategies.includes(s);
                const isMl = spaces?.[s]?.is_ml;
                // OPT-011 — badges TF recommandés (cyan) + warning si un TF
                // sélectionné n'est pas dans la liste recommandée (ambre ⚠).
                const recTfs = recommendedTfsFor(s);
                const hasWarn = active && hasNonRecommendedTf(s);
                return (
                  <button
                    key={s}
                    onClick={() => toggle(selectedStrategies, s, setSelectedStrategies)}
                    className={cn(
                      'px-3 py-1.5 rounded-lg text-xs font-mono border transition-all inline-flex items-center gap-1',
                      active
                        ? 'bg-primary-500/15 text-primary-400 border-primary-500/40'
                        : 'bg-card-hover text-muted border-border hover:border-border-hi',
                    )}
                  >
                    {s}
                    {isMl && <span className="ml-1 text-purple-400">ML</span>}
                    {active && recTfs.length > 0 && (
                      <span className="ml-1 inline-flex gap-0.5">
                        {recTfs.slice(0, 3).map((tf) => (
                          <span
                            key={tf}
                            className="px-1 rounded text-[0.55rem] bg-cyan-500/15 text-cyan-300 border border-cyan-500/30"
                            title={`TF recommandé : ${tf}`}
                          >
                            {tf}
                          </span>
                        ))}
                        {hasWarn && (
                          <span
                            className="px-1 rounded text-[0.55rem] bg-amber-500/15 text-amber-300 border border-amber-500/30"
                            title="Au moins un TF sélectionné n'est pas recommandé pour cette stratégie"
                          >
                            ⚠
                          </span>
                        )}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            {/* ML-001 — en mode ML sans stratégies chargées (espaces non
                encore disponibless ou aucune strat ML déclarée), un message
                évite d'avoir une zone vide sans explication. */}
            {filterMl && visibleStrategies.length === 0 && (
              <p className="text-[11px] text-muted italic mt-2">
                Aucune stratégie ML déclarée dans <code className="font-mono">/optimize/spaces</code>.
              </p>
            )}
          </div>

          {/* Timeframes (multi) */}
          <div>
            <div className="text-xs text-dim mb-2">Timeframes (multi)</div>
            <TimeframeButtons
              multi
              values={selectedTfs}
              onChangeMulti={setSelectedTfs}
            />
            {/* OPT-005 — hint IS/OOS pour chaque TF coché. Calcule la
                répartition 65/35 et signale le plafond OKX (8000 1h ≈ 333j). */}
            {limitPerTf > 0 && selectedTfs.length > 0 && (
              <div className="mt-2 space-y-0.5">
                {selectedTfs.map((tf) => {
                  const h = isOosHint(limitPerTf, tf);
                  return (
                    <div
                      key={tf}
                      className={cn(
                        'text-[10px] font-mono',
                        h.capped ? 'text-amber-400' : 'text-dim',
                      )}
                    >
                      {h.text}
                    </div>
                  );
                })}
              </div>
            )}
            {/* ML-006 — les recettes omnibus (e.g. `opus_omnibus_v11`)
                entraînent un LightGBM multi-tâches sur toutes les têtes : un
                entraînement fiable exige ≥ 2200 bougies. Sans ce warning,
                l'utilisateur peut soumettre un run sur 1500 bougies et obtenir
                un AUC trompeur (surapprentissage sur peu d'échantillons). */}
            {filterMl && hasOmnibusSelected && (
              <div className="mt-2 text-[10px] text-amber-400 flex items-start gap-1.5">
                <span aria-hidden>⚠</span>
                <span>
                  Les recettes omnibus exigent ≥ 2200 bougies pour un entraînement fiable.
                </span>
              </div>
            )}
          </div>

          {/* Symbols — P1-7 : dynamiques depuis config (plus hardcodés) */}
          <div>
            <div className="text-xs text-dim mb-2">
              Symboles
              {availableSymbols !== FALLBACK_SYMBOLS && (
                <span className="text-[10px] text-emerald-400 ml-1">(depuis config)</span>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              {availableSymbols.map((sym) => {
                const active = selectedSymbols.includes(sym);
                return (
                  <button
                    key={sym}
                    onClick={() => toggle(selectedSymbols, sym, setSelectedSymbols)}
                    className={cn(
                      'px-3 py-1.5 rounded-lg text-xs font-mono border transition-all',
                      active
                        ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40'
                        : 'bg-card-hover text-muted border-border hover:border-border-hi',
                    )}
                  >
                    {sym}
                  </button>
                );
              })}
            </div>
          </div>

          {/* P1-2 : Presets d'optimisation (Rapide/Équilibré/Approfondi).
              L'utilisateur clique sur un preset pour set nTrials/nJobs/etc.
              en une fois. Modifier manuellement un champ passe en mode Custom. */}
          <div>
            <div className="text-xs text-dim mb-2">Presets</div>
            <div className="flex flex-wrap gap-2">
              {(Object.keys(PRESETS) as PresetKey[]).map((key) => {
                const p = PRESETS[key];
                const active = activePreset === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => applyPreset(key)}
                    className={cn(
                      'px-3 py-1.5 rounded-lg text-xs border transition-all text-left',
                      active
                        ? 'bg-cyan-500/15 text-cyan-400 border-cyan-500/40'
                        : 'bg-card-hover text-muted border-border hover:border-border-hi',
                    )}
                    title={p.description}
                  >
                    <div className="font-semibold">{p.label}</div>
                    <div className="text-[10px] text-dim">{p.description}</div>
                  </button>
                );
              })}
              {activePreset === null && (
                <div className="px-3 py-1.5 rounded-lg text-xs border bg-amber-500/10 text-amber-400 border-amber-500/30">
                  <div className="font-semibold">Custom</div>
                  <div className="text-[10px] text-dim">Configuration manuelle</div>
                </div>
              )}
            </div>
          </div>

          {/* Method + numerics */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div>
              <label className="text-xs text-dim block mb-1.5">Méthode</label>
              <select
                aria-label="Méthode"
                value={method}
                onChange={(e) => setMethod(e.target.value as (typeof METHODS)[number])}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
              >
                {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">n_trials</label>
              <input
                aria-label="n_trials"
                type="number"
                min={5}
                max={500}
                value={nTrials}
                onChange={(e) => { setNTrials(Math.max(1, Number(e.target.value) || 1)); markCustom(); }}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">n_jobs</label>
              <input
                aria-label="n_jobs"
                type="number"
                min={1}
                max={16}
                value={nJobs}
                onChange={(e) => { setNJobs(Math.max(1, Number(e.target.value) || 1)); markCustom(); }}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm cursor-pointer h-10">
                <input
                  type="checkbox"
                  checked={autoApply}
                  onChange={(e) => setAutoApply(e.target.checked)}
                  className="rounded"
                />
                Auto-apply
              </label>
            </div>
            <div className="flex items-end">
              <label
                className="flex items-center gap-2 text-sm cursor-pointer h-10"
                title="Gel des paramètres à faible impact pendant la recherche (dépistage dans le budget d'essais, espaces larges uniquement)"
              >
                <input
                  type="checkbox"
                  checked={paramSearchOptim}
                  onChange={(e) => setParamSearchOptim(e.target.checked)}
                  className="rounded"
                />
                Param Search Optim
              </label>
            </div>
          </div>

          {/* P1-9 — Options avancées collapsible (early_stopping, limit_per_tf,
              ml_tune_hp). Replié par défaut pour désencombrer la config. */}
          <details className="pt-2 border-t border-border">
            <summary className="text-[10px] uppercase tracking-wider text-dim font-semibold cursor-pointer hover:text-foreground py-2">
              Options avancées
            </summary>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4 pt-2">
            <div>
              <label
                className="text-xs text-dim block mb-1.5"
                title="Arrête la recherche si aucun gain de score n'est observé pendant N essais. 0 = désactivé."
              >
                early_stopping (patience)
              </label>
              <input
                aria-label="early_stopping"
                type="number"
                min={0}
                max={50}
                value={earlyStopping}
                onChange={(e) => setEarlyStopping(Math.max(0, Math.min(50, Number(e.target.value) || 0)))}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div>
              <label
                className="text-xs text-dim block mb-1.5"
                title="Nombre de bougies demandées par TF. 0 = auto (calculé selon les stratégies)."
              >
                limit_per_tf
              </label>
              <input
                aria-label="limit_per_tf"
                type="number"
                min={0}
                max={8000}
                step={100}
                value={limitPerTf}
                onChange={(e) => setLimitPerTf(Math.max(0, Math.min(8000, Number(e.target.value) || 0)))}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div className="flex items-end">
              {/* ML-002 — en mode ML, le checkbox n'est visible QUE si une
                  stratégie ML est sélectionnée (sinon il n'a aucun sens et
                  l'utilisateur le verrait grillé sans comprendre pourquoi).
                  Hors mode ML, on garde le comportement existant : toujours
                  visible mais désactivé si aucune strat ML n'est sélectionnée.
                  Le label « ml_tune_hp » reste technique hors ML ; en ML on
                  affiche la description fonctionnelle complète pour rendre
                  évident que cocher allonge la durée (two-phase). */}
              {(!filterMl || hasMlSelected) && (
                <label
                  className={cn(
                    'flex items-center gap-2 text-sm cursor-pointer h-10',
                    !hasMlSelected && 'opacity-50 cursor-not-allowed',
                  )}
                  title={
                    hasMlSelected
                      ? 'Réglage des hyperparamètres du modèle ML (n_estimators, learning_rate…) en plus des params de stratégie.'
                      : 'Sélectionnez une stratégie ML pour activer cette option.'
                  }
                >
                  <input
                    type="checkbox"
                    checked={mlTuneHp}
                    onChange={(e) => setMlTuneHp(e.target.checked)}
                    disabled={!hasMlSelected}
                    className="rounded"
                  />
                  {filterMl
                    ? 'Régler aussi les hyperparamètres d\'entraînement (two-phase, plus lent)'
                    : 'ml_tune_hp'}
                </label>
              )}
            </div>
            </div>
          </details>

          {/* Preview matrice strat × TF × symbole (parité Jinja2) */}
          {selectedStrategies.length > 0 && selectedTfs.length > 0 && selectedSymbols.length > 0 && (
            <div className="rounded-lg border border-border bg-card-hover/50 p-3 space-y-2">
              <div className="text-[11px] text-dim uppercase tracking-wider font-semibold">
                Preview run — {selectedStrategies.length}×{selectedTfs.length}×{selectedSymbols.length}
                {' = '}
                <span className="text-foreground font-mono">
                  {selectedStrategies.length * selectedTfs.length * selectedSymbols.length}
                </span>
                {' '}slots
              </div>
              <div className="overflow-x-auto max-h-40">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-dim border-b border-border">
                      <th className="text-left p-1.5 font-medium">Stratégie</th>
                      {selectedTfs.map((tf) => (
                        <th key={tf} className="p-1.5 font-mono font-medium text-center">{tf}</th>
                      ))}
                      <th className="p-1.5 font-medium text-right">Combos</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedStrategies.map((s) => {
                      const info = spaces?.[s];
                      const recTfs: string[] = info?.recommended_tfs ?? info?.timeframes ?? [];
                      return (
                        <tr key={s} className="border-b border-border/40">
                          <td className="p-1.5 font-mono font-semibold">{s}</td>
                          {selectedTfs.map((tf) => {
                            const rec = recTfs.length === 0 || recTfs.includes(tf);
                            return (
                              <td key={tf} className="p-1.5 text-center">
                                <span className={cn(
                                  'inline-block w-2 h-2 rounded-full',
                                  rec ? 'bg-emerald-400' : 'bg-amber-400/70',
                                )} title={rec ? 'TF recommandé' : 'TF hors liste recommandée'} />
                              </td>
                            );
                          })}
                          <td className="p-1.5 text-right font-mono text-muted">
                            {info?.n_combos ?? '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] text-dim">
                Point vert = TF recommandé pour la stratégie · ambre = hors liste (sera tout de même lancé).
                Symboles : {selectedSymbols.join(', ')} · méthode {method} · {nTrials} trials · {nJobs} worker(s)
              </p>
            </div>
          )}

          {/* OPT-008 — feedback immédiat : carte ambre "Récupération…" pendant
              l'appel, puis carte verte "✓ N job(s) lancé(s)" avec le détail
              des bougies reçues par TF et les combinaisons ignorées. */}
          {launchFeedback?.kind === 'fetching' && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300 flex items-center gap-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Récupération des bougies en cours…
            </div>
          )}
          {launchFeedback?.kind === 'ok' && (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300 space-y-1">
              <div className="flex items-center gap-2 font-semibold">
                <CheckCircle2 className="w-3.5 h-3.5" />
                ✓ {launchFeedback.nJobs} job(s) lancé(s)
              </div>
              {launchFeedback.receivedBars && Object.keys(launchFeedback.receivedBars).length > 0 && (
                <div className="font-mono text-[0.65rem] opacity-80">
                  Bougies reçues :{' '}
                  {Object.entries(launchFeedback.receivedBars).map(([tf, n]) => `${tf}=${n}`).join(' · ')}
                </div>
              )}
              {Array.isArray(launchFeedback.skipped) && launchFeedback.skipped.length > 0 && (
                <div className="font-mono text-[0.65rem] text-amber-300">
                  ⚠ {launchFeedback.skipped.length} combinaison(s) ignorée(s) (espace vide ou stratégie sans params).
                </div>
              )}
            </div>
          )}

          <Button
            onClick={handleStart}
            disabled={startOptimize.isPending}
            variant="primary"
          >
            {startOptimize.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4" fill="currentColor" />
            )}
            {/* ML-001 — label dédié en mode ML : le bouton primary est déjà
                cyan par le thème (cf. button.tsx), on ne change que le texte. */}
            {filterMl ? '⬡ Lancer l\'optimisation ML' : 'Lancer l\'optimisation'}
          </Button>

          {/* OPT-010 — rappel des params globaux non modifiés par l'optimiseur.
              Sans lui, un utilisateur peut penser que `score_threshold` est
              optimisé alors qu'il ne l'est pas (laissant un faux espoir d'edge
              mesuré alors que le seuil reste celui du YAML). */}
          <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 px-3 py-2 text-xs text-cyan-200 flex items-start gap-2">
            <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            <div>
              Les paramètres globaux ne sont jamais modifiés par l&apos;optimiseur :{' '}
              <code className="font-mono">score_threshold</code>,{' '}
              <code className="font-mono">risk_per_trade</code>,{' '}
              <code className="font-mono">capital</code>,{' '}
              <code className="font-mono">timeframe</code>,{' '}
              <code className="font-mono">paper_mode</code>.
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Param spaces */}
      {!spacesLoading && <ParamSpaceTable spaces={spaces || {}} />}

      {/* Active results by TF */}
      <Card>
        <CardHeader>
          <CardTitle>Résultats actifs par TF</CardTitle>
          <Badge variant="success">appliqués</Badge>
        </CardHeader>
        <CardContent>
          {Object.keys(activeResults).length === 0 ? (
            <div className="text-sm text-muted text-center py-6">Aucun résultat actif</div>
          ) : (
            <div className="space-y-2">
              {Object.entries(activeResults).map(([tf, strategies]) => (
                <div
                  key={tf}
                  className="flex items-center justify-between p-3 rounded-lg bg-card-hover border border-border"
                >
                  <span className="font-mono font-semibold text-sm">{tf}</span>
                  <div className="flex flex-wrap gap-1">
                    {(strategies as string[]).map((s) => (
                      <Badge key={s} variant="info">{s}</Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Jobs list — OPT-007 : grouped by status, collapsible, with a
          "Tout ouvrir / Réduire tout" toggle.
          P1-6 : filtres/recherche + P2-5 : export CSV */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">Jobs</h2>
          <div className="flex items-center gap-3">
            {/* P2-5 : export CSV */}
            {filteredJobs.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                onClick={exportJobsCsv}
                className="h-7 text-xs"
                title="Exporter les jobs en CSV"
              >
                <FileDown className="w-3 h-3" />
                CSV
              </Button>
            )}
            {/* P1-10 : bouton mode comparaison */}
            {filteredJobs.length >= 2 && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => { setCompareMode(!compareMode); if (compareMode) setCompareIds([]); }}
                className={cn('h-7 text-xs', compareMode && 'text-cyan-400')}
                title="Comparer les jobs côte à côte (max 4)"
              >
                <GitCompare className="w-3 h-3" />
                {compareMode ? `Annuler (${compareIds.length})` : 'Comparer'}
              </Button>
            )}
            {filteredJobs.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setAllExpanded((v) => !v)}
                className="h-7 text-xs"
                aria-label={allExpanded ? 'Réduire toutes les cartes' : 'Déplier toutes les cartes'}
              >
                {allExpanded ? 'Réduire tout' : 'Tout ouvrir'}
              </Button>
            )}
            <span className="text-xs text-dim font-mono">
              {filteredJobs.length}{jobsSearch || jobsStatusFilter ? `/${jobs.length}` : ''}
            </span>
          </div>
        </div>
        {/* P1-6 : filtres/recherche sur les jobs */}
        {jobs.length > 3 && (
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <input
              type="text"
              value={jobsSearch}
              onChange={(e) => setJobsSearch(e.target.value)}
              placeholder="Rechercher (stratégie, symbole, job_id…)"
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-xs w-56"
            />
            <div className="flex items-center gap-1">
              {['', 'running', 'done', 'error', 'cancelled'].map((s) => (
                <button
                  key={s}
                  onClick={() => setJobsStatusFilter(s)}
                  className={cn(
                    'text-[10px] px-2 py-0.5 rounded-md border transition-colors',
                    jobsStatusFilter === s
                      ? 'bg-primary-500/10 border-primary-500/30 text-primary-400'
                      : 'bg-card-hover/30 border-border text-muted hover:text-foreground',
                  )}
                >
                  {s === '' ? 'Tous' : STATUS_LABEL[s] ?? s}
                </button>
              ))}
            </div>
          </div>
        )}
        {jobsLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
          </div>
        ) : jobsError ? (
          <Card>
            <CardContent className="text-center py-8 text-red-400 text-sm">
              <XCircle className="w-8 h-8 mx-auto mb-2" />
              {jobsError}
            </CardContent>
          </Card>
        ) : jobs.length === 0 ? (
          <Card>
            <CardContent className="text-center py-12 text-muted text-sm">
              <Zap className="w-8 h-8 mx-auto mb-2 text-dim" />
              Aucun job d&apos;optimisation. Lancez-en un ci-dessus.
            </CardContent>
          </Card>
        ) : jobGroups.length === 0 ? (
          <Card>
            <CardContent className="text-center py-12 text-muted text-sm">
              <Zap className="w-8 h-8 mx-auto mb-2 text-dim" />
              Aucun job d&apos;optimisation. Lancez-en un ci-dessus.
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-4">
            {jobGroups.map((g) => {
              const isCollapsed = collapsedGroups[g.key] ?? !g.defaultExpanded;
              return (
                <div key={g.key}>
                  <button
                    type="button"
                    onClick={() => toggleGroup(g.key)}
                    className="w-full flex items-center gap-2 py-1.5 text-xs uppercase tracking-wider text-muted hover:text-foreground"
                    aria-expanded={!isCollapsed}
                  >
                    {isCollapsed
                      ? <ChevronRight className="w-3.5 h-3.5" />
                      : <ChevronDown className="w-3.5 h-3.5" />}
                    <span className="font-semibold">{g.label}</span>
                    <Badge variant="default" className="text-[0.55rem]">{g.jobs.length}</Badge>
                  </button>
                  {!isCollapsed && (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-2">
                      {g.jobs.map((job) => (
                        <JobCard
                          key={job.job_id}
                          job={job}
                          defaultExpanded={allExpanded || g.key === 'running'}
                          filterMl={filterMl}
                          compareMode={compareMode}
                          compareSelected={compareIds.includes(job.job_id)}
                          onCompareToggle={toggleCompare}
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* P1-10 : Panneau de comparaison des jobs (max 4).
            Affiché quand au moins 2 jobs sont sélectionnés en mode comparaison. */}
        {compareMode && compareJobs.length >= 2 && (
          <Card className="border-cyan-500/30">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <GitCompare className="w-4 h-4 text-cyan-400" />
                Comparaison ({compareJobs.length}/4)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-dim border-b border-border">
                      <th className="p-2">Métrique</th>
                      {compareJobs.map((j) => (
                        <th key={j.job_id} className="p-2 text-right font-mono">
                          {j.strategy}<span className="text-dim">@{j.timeframe}</span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-b border-border/30">
                      <td className="p-2 text-muted">OOS Score</td>
                      {compareJobs.map((j) => (
                        <td key={j.job_id} className="p-2 text-right font-mono text-emerald-400">
                          {(j.result?.best_oos_score ?? 0).toFixed(4)}
                        </td>
                      ))}
                    </tr>
                    <tr className="border-b border-border/30">
                      <td className="p-2 text-muted">OOS PnL</td>
                      {compareJobs.map((j) => (
                        <td key={j.job_id} className="p-2 text-right font-mono">
                          {formatUSD(j.result?.best_oos_pnl ?? 0)}
                        </td>
                      ))}
                    </tr>
                    <tr className="border-b border-border/30">
                      <td className="p-2 text-muted">Trades</td>
                      {compareJobs.map((j) => (
                        <td key={j.job_id} className="p-2 text-right font-mono">
                          {j.result?.best_oos_trades ?? 0}
                        </td>
                      ))}
                    </tr>
                    <tr className="border-b border-border/30">
                      <td className="p-2 text-muted">Sharpe</td>
                      {compareJobs.map((j) => (
                        <td key={j.job_id} className="p-2 text-right font-mono">
                          {(j.result?.best_oos_sharpe ?? 0).toFixed(2)}
                        </td>
                      ))}
                    </tr>
                    <tr className="border-b border-border/30">
                      <td className="p-2 text-muted">Overfit</td>
                      {compareJobs.map((j) => (
                        <td key={j.job_id} className={`p-2 text-right font-mono ${(j.result?.overfit ?? 0) > 2.5 ? 'text-amber-400' : ''}`}>
                          {(j.result?.overfit ?? 0).toFixed(2)}
                        </td>
                      ))}
                    </tr>
                    {compareJobs.some((j) => j.deflated_sharpe != null) && (
                      <tr className="border-b border-border/30">
                        <td className="p-2 text-muted">Deflated Sharpe</td>
                        {compareJobs.map((j) => (
                          <td key={j.job_id} className="p-2 text-right font-mono">
                            {j.deflated_sharpe != null ? `${(j.deflated_sharpe * 100).toFixed(1)}%` : '—'}
                          </td>
                        ))}
                      </tr>
                    )}
                    {compareJobs.some((j) => j.wf_consistency != null) && (
                      <tr className="border-b border-border/30">
                        <td className="p-2 text-muted">WF Consistency</td>
                        {compareJobs.map((j) => (
                          <td key={j.job_id} className="p-2 text-right font-mono">
                            {j.wf_consistency != null ? `${j.wf_consistency.toFixed(0)}%` : '—'}
                          </td>
                        ))}
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}

        {/* P0-5 — Historique des optimisations (changelog des apply).
            Replié par défaut — l'utilisateur le déplie pour voir l'historique. */}
        <OptimizerHistory />
      </div>
    </div>
  );
}
