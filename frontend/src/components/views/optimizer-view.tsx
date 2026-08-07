'use client';

/**
 * Onglet Optimizer de `/lab` — optimisation bayésienne / grid / random.
 *
 * Lot Laboratoire : cette vue était la page `/optimizer`, que l'onglet se
 * contentait de teaser (« Aller à l'optimiseur existant », « intégration
 * native prévue au Sprint 7 »). `/optimizer` est désormais en 308 vers
 * `/lab?tab=optimizer`.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
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
  useApplyOptimize,
  useCancelOptimize,
  useDeleteOptimizeJob,
} from '@/hooks/use-api';
import {
  Play, Loader2, CheckCircle2, XCircle, Trash2, StopCircle,
  Sparkles, Cpu, Layers, Activity, Zap, ChevronDown, ChevronRight, Info,
} from 'lucide-react';
import type { OptimizeJob, OptimizeSpaces } from '@/types';
import { CostModelCard } from '@/components/cards/cost-model-card';
import { BeforeAfterGrid } from '@/components/cards/before-after-grid';
import { TopTrialsTable } from '@/components/tables/top-trials-table';
import { OptimizerWarnings } from '@/components/cards/optimizer-warnings';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { isOosHint } from '@/lib/limit-hint';
import { normalizeBaseline, deriveAfter, normalizeTopTrials } from '@/lib/backend-normalizers';

// ── Helpers ─────────────────────────────────────────────────────────────────

const METHODS = ['grid', 'random', 'bayesian'] as const;
const ALL_SYMBOLS = ['BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'BNB/USDC', 'XRP/USDC'];

const STATUS_VARIANT: Record<string, 'success' | 'danger' | 'warning' | 'info' | 'default'> = {
  pending: 'warning',
  running: 'info',
  done: 'success',
  error: 'danger',
  cancelled: 'default',
};

const STATUS_LABEL: Record<string, string> = {
  pending: 'En attente',
  running: 'En cours',
  done: 'Terminé',
  error: 'Erreur',
  cancelled: 'Annulé',
};

function LiveProgress({ job }: { job: OptimizeJob }) {
  const [live, setLive] = useState<Partial<OptimizeJob> | null>(null);
  // OPT-006 — pour l'ETA on a besoin du timestamp du premier trial reçu.
  // Sans lui, on ne saurait pas combien de temps a pris chaque trial.
  const firstTrialAtRef = useRef<number | null>(null);
  const firstTrialDoneRef = useRef<number | null>(null);

  useEffect(() => {
    if (job.status !== 'running') {
      setLive(null);
      // Reset des refs à la fermeture pour qu'un éventuel second run reparte
      // d'un ETA vierge.
      firstTrialAtRef.current = null;
      firstTrialDoneRef.current = null;
      return;
    }
    const url = api.optimizeStreamUrl(job.job_id);
    // withCredentials : envoie le cookie HttpOnly api_key même en dev
    // (frontend/backend sur des ports différents = origines distinctes) —
    // S1-05, plus de clé API en query string.
    const es = new EventSource(url, { withCredentials: true });
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setLive(data);
        if (data.status === 'done' || data.status === 'error' || data.status === 'cancelled') {
          es.close();
        }
      } catch {
        // ignore malformed SSE frames
      }
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [job.job_id, job.status]);

  const progress = live?.progress ?? job.progress ?? 0;
  const bestScore = live?.best_score ?? job.best_score ?? 0;
  const trialsDone = live?.trials_done ?? job.trials_done ?? 0;
  const nTrials = live?.n_trials ?? job.n_trials ?? 0;
  const isRunning = (live?.status ?? job.status) === 'running';

  // OPT-006 — ETA = (nTrials - trialsDone) * avg_trial_duration.
  // avg_trial_duration = (now - firstTrialAt) / (trialsDone - firstTrialDone).
  // On cache le timestamp du premier trial vu pour la première fois ; on
  // ignore les 5 premières secondes (warmup) pour ne pas afficher un ETA
  // erratique sur 1-2 trials.
  let etaText: string | null = null;
  const startTs = job.started_at ? job.started_at * 1000 : null;
  const elapsedMs = startTs ? Date.now() - startTs : 0;
  if (isRunning && trialsDone > 0 && elapsedMs > 5000 && nTrials > 0) {
    const avgPerTrialMs = elapsedMs / Math.max(1, trialsDone);
    const remainingMs = (nTrials - trialsDone) * avgPerTrialMs;
    if (remainingMs < 60_000) {
      etaText = `~${Math.max(1, Math.ceil(remainingMs / 1000))}s restant`;
    } else {
      etaText = `~${Math.ceil(remainingMs / 60_000)}min restant`;
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted">
          {isRunning && <Loader2 className="w-3 h-3 inline animate-spin mr-1" />}
          Trials: <span className="font-mono text-foreground">{trialsDone}/{nTrials || '∞'}</span>
        </span>
        <span className="text-muted">
          Best score: <span className="font-mono text-primary-400">{bestScore.toFixed(4)}</span>
        </span>
        <span className="font-mono text-muted">
          {progress.toFixed(1)}%
          {etaText && <span className="ml-2 text-amber-400">{etaText}</span>}
        </span>
      </div>
      <div className="h-1.5 bg-card-hover rounded-full overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all',
            isRunning ? 'bg-primary-400' : job.status === 'done' ? 'bg-emerald-400' : 'bg-dim',
          )}
          style={{ width: `${Math.min(progress, 100)}%` }}
        />
      </div>
    </div>
  );
}

// ── Param space recap ───────────────────────────────────────────────────────

function ParamSpaceTable({ spaces }: { spaces: OptimizeSpaces }) {
  const entries = Object.entries(spaces || {});
  if (entries.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Espaces de paramètres</CardTitle>
        <Badge variant="info">{entries.length} stratégies</Badge>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-dim border-b border-border">
                <th className="p-3 font-medium">Stratégie</th>
                <th className="p-3 font-medium">Type</th>
                <th className="p-3 font-medium">Paramètres</th>
                <th className="p-3 font-medium text-right">Combinaisons</th>
                <th className="p-3 font-medium">Timeframes</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(([name, info]) => (
                <tr key={name} className="border-b border-border/30 hover:bg-card-hover">
                  <td className="p-3 font-mono font-semibold">{name}</td>
                  <td className="p-3">
                    <Badge variant={info.is_ml ? 'purple' : 'default'}>
                      {info.is_ml ? 'ML' : 'Classique'}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs text-muted">
                    {info.params ? Object.keys(info.params).join(', ') : '—'}
                  </td>
                  <td className="p-3 text-right font-mono">{info.n_combos ?? '—'}</td>
                  <td className="p-3">
                    <div className="flex flex-wrap gap-1">
                      {(info.timeframes || []).map((tf) => (
                        <Badge key={tf} variant="default">{tf}</Badge>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Job card ────────────────────────────────────────────────────────────────

function JobCard({
  job,
  defaultExpanded,
  filterMl = false,
}: {
  job: OptimizeJob;
  defaultExpanded?: boolean;
  /** ML-004/ML-007 — mode ML : le tooltip Apply mentionne l'entraînement du
   *  modèle, et la note « modèle sauvegardé automatiquement » s'affiche près
   *  du bouton Apply. En cas de succès, on invalide aussi les queries
   *  `mlInfo` et `ml-recipes` pour que la StrategyTable du tab ML se
   *  rafraîchisse (le backend réentraîne le modèle pendant l'apply). */
  filterMl?: boolean;
}) {
  const apply = useApplyOptimize();
  const cancel = useCancelOptimize();
  const del = useDeleteOptimizeJob();
  const qc = useQueryClient();

  // OPT-007 — carte repliable : dépliée par défaut pour les jobs en cours,
  // repliée pour les jobs terminés/annulés en erreur (le verdict OOS tient
  // en 5 KPIs dans l'en-tête ; le détail est volumineux).
  const [expanded, setExpanded] = useState<boolean>(defaultExpanded ?? job.status === 'running');

  const handleApply = async () => {
    try {
      await apply.mutateAsync({ jobId: job.job_id });
      toast.success('Paramètres appliqués au slot');
      // ML-004 — en mode ML, l'apply déclenche aussi l'entraînement du modèle
      // côté backend. On invalide les queries consommées par le tab ML pour
      // que la StrategyTable (statut Entraîné/Non entraîné, AUC) se
      // rafraîchisse sans attendre le prochain poll de 30 s.
      if (filterMl) {
        qc.invalidateQueries({ queryKey: ['mlInfo'] });
        qc.invalidateQueries({ queryKey: ['ml-recipes'] });
      }
    } catch (e: any) {
      toast.error(`Apply failed: ${e.message}`);
    }
  };

  const handleCancel = async () => {
    try {
      await cancel.mutateAsync(job.job_id);
      toast.success('Job annulé');
    } catch (e: any) {
      toast.error(`Cancel failed: ${e.message}`);
    }
  };

  const handleDelete = async () => {
    try {
      await del.mutateAsync(job.job_id);
      toast.success('Job supprimé');
    } catch (e: any) {
      toast.error(`Delete failed: ${e.message}`);
    }
  };

  const variant = STATUS_VARIANT[job.status] || 'default';
  const isRunning = job.status === 'running';
  const isDone = job.status === 'done';
  const result = job.result || {};

  // OPT-001/002 — détail avant/après + top-5 trials. Reconstruits via les
  // normalizers car le backend ne renvoie pas toujours le bloc `after` (cf.
  // audit §3.2) et `top5` est l'alias legacy de `top_trials`.
  const baseline = normalizeBaseline(job.baseline);
  const after = deriveAfter(job);
  const trials = normalizeTopTrials(job.result);
  const baselineSource = (job.baseline as any)?.baseline_source ?? (job.baseline as any)?.source;

  return (
    <Card className="space-y-3">
      <CardContent className="space-y-3">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex-1 min-w-0 text-left"
            aria-expanded={expanded}
            aria-label={expanded ? 'Réduire la carte job' : 'Déplier la carte job'}
          >
            <div className="flex items-center gap-2">
              {expanded
                ? <ChevronDown className="w-3.5 h-3.5 text-muted flex-shrink-0" />
                : <ChevronRight className="w-3.5 h-3.5 text-muted flex-shrink-0" />}
              <Cpu className="w-4 h-4 text-primary-400 flex-shrink-0" />
              <span className="font-mono text-sm font-semibold truncate">{job.strategy}</span>
              <span className="text-xs text-muted">{job.timeframe}</span>
              {job.symbol && <span className="text-xs text-dim">· {job.symbol}</span>}
            </div>
            <div className="text-[10px] text-dim mt-1 font-mono pl-6">
              {job.job_id} · {job.method} · started {job.started_at ? timeAgoShort(job.started_at) : '—'}
            </div>
          </button>
          <Badge variant={variant}>{STATUS_LABEL[job.status] || job.status}</Badge>
        </div>

        {/* Progress — toujours visible (même replié, l'utilisateur voit si ça tourne). */}
        <LiveProgress job={job} />

        {expanded && (
          <>
            {/* Result */}
            {isDone && result.best_params && (
              <div className="rounded-lg bg-card-hover border border-border p-3 space-y-2">
                <div className="flex items-center gap-2 text-xs text-emerald-400 font-semibold">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Résultat OOS
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                  <Metric label="Score" value={(result.best_oos_score ?? 0).toFixed(4)} />
                  <Metric label="PnL" value={formatUSD(result.best_oos_pnl ?? 0)} />
                  <Metric label="Trades" value={String(result.best_oos_trades ?? 0)} />
                  <Metric label="Win rate" value={`${((result.best_oos_wr ?? 0) * 100).toFixed(1)}%`} />
                  <Metric label="Sharpe" value={(result.best_oos_sharpe ?? 0).toFixed(2)} />
                  <Metric label="Apply" value={job.applied ? 'Oui' : 'Non'} />
                </div>
              </div>
            )}

            {/* OPT-003 — warnings anti-surapprentissage (overfit, OOS trades, score). */}
            {isDone && (
              <OptimizerWarnings
                overfit={result.overfit}
                oosTrades={result.best_oos_trades}
                oosScore={result.best_oos_score}
              />
            )}

            {/* OPT-001 — Avant/Après (baseline vs OOS après optimisation). */}
            {isDone && (baseline || after) && (
              <BeforeAfterGrid
                baseline={baseline}
                baselineSource={baselineSource}
                after={after}
              />
            )}

            {/* OPT-002 — Top-5 trials + best params. */}
            {isDone && (trials.length > 0 || result.best_params) && (
              <TopTrialsTable
                trials={trials}
                bestParams={result.best_params ?? null}
              />
            )}

            {/* Contexte facturé pendant l'optimisation : un `oos_score` n'est pas
                comparable d'un run à l'autre sans lui — deux scores très différents
                peuvent ne différer que par la venue résolue (spot vs margin). */}
            {isDone && result.cost_model && <CostModelCard model={result.cost_model} />}

            {/* Error */}
            {job.status === 'error' && job.error && (
              <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded p-2">
                {job.error}
              </div>
            )}
          </>
        )}

        {/* Actions */}
        <div className="space-y-2 pt-1 border-t border-border">
          {/* ML-007 — note « modèle ML sauvegardé automatiquement » près du
              bouton Apply : rappelle que l'apply n'écrase que les params
              optimisés (le modèle est géré par le backend). */}
          {filterMl && isDone && !job.applied && (
            <p className="text-[10px] text-cyan-300/80 italic">
              Écrase uniquement les params optimisés — le modèle ML est sauvegardé automatiquement.
            </p>
          )}
          <div className="flex gap-2">
            {isDone && !job.applied && (
              <Button
                size="sm"
                variant="success"
                onClick={handleApply}
                disabled={apply.isPending}
                className="flex-1"
                // ML-004 — en mode ML, l'apply entraîne aussi le modèle : le
                // tooltip l'indique pour éviter la confusion avec un apply
                // classique qui ne touche qu'aux params.
                title={filterMl ? 'Applique les params + entraîne le modèle' : undefined}
              >
                <CheckCircle2 className="w-3 h-3" />
                Apply
              </Button>
            )}
            {isRunning && (
              <Button
                size="sm"
                variant="danger"
                onClick={handleCancel}
                disabled={cancel.isPending}
                className="flex-1"
              >
                <StopCircle className="w-3 h-3" />
                Annuler
              </Button>
            )}
            {!isRunning && (
              <Button
                size="sm"
                variant="ghost"
                onClick={handleDelete}
                disabled={del.isPending}
                // Bouton à icône seule : sans nom accessible, axe le remonte en
                // `button-name` (critique) et un lecteur d'écran n'annonce que
                // « bouton ». C'est l'item #26 de l'audit, donné pour traité en
                // S2 mais jamais appliqué ici — et le job a11y de la CI est
                // depuis passé bloquant.
                aria-label="Supprimer ce job d'optimisation"
                title="Supprimer ce job"
              >
                <Trash2 className="w-3 h-3" />
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function timeAgoShort(unixSec: number): string {
  const secs = Math.floor(Date.now() / 1000 - unixSec);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}min`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}j`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-dim">{label}</div>
      <div className="font-mono font-semibold">{value}</div>
    </div>
  );
}

// ── Vue ─────────────────────────────────────────────────────────────────────

/**
 * ML-001 — `filterMl` restreint la vue aux stratégies ML et active les
 * affordances dédiées (label de bouton cyan « ⬇ Lancer l'optimisation ML »,
 * warning omnibus ML-006, ml_tune_hp labellisé, note Apply ML-007). Utilisé
 * par `ml-view.tsx` pour intégrer l'optimiseur ML directement dans l'onglet
 * ML au lieu de renvoyer vers l'onglet Optimizer.
 */
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

  // Form state
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [selectedTfs, setSelectedTfs] = useState<string[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(['BTC/USDC']);
  const [method, setMethod] = useState<(typeof METHODS)[number]>('bayesian');
  const [nTrials, setNTrials] = useState(60);
  const [nJobs, setNJobs] = useState(1);
  const [autoApply, setAutoApply] = useState(false);
  const [paramSearchOptim, setParamSearchOptim] = useState(true);
  // OPT-004 — trois nouvelles options d'optimisation. `early_stopping` et
  // `limit_per_tf` sont des inputs numériques ; `ml_tune_hp` n'est visible
  // que si une stratégie ML est sélectionnée.
  const [earlyStopping, setEarlyStopping] = useState(0);
  const [limitPerTf, setLimitPerTf] = useState(0);
  const [mlTuneHp, setMlTuneHp] = useState(false);

  // OPT-007 — état global "tout déplié / tout replié". Bascule l'état par
  // défaut passé aux JobCards ; le toggle lui-même est au-dessus de la grille.
  const [allExpanded, setAllExpanded] = useState(false);

  // OPT-008 — feedback immédiat après « Lancer l'optimisation » :
  // carte ambre "Récupération des bougies en cours…" pendant l'appel, puis
  // carte verte "✓ N job(s) lancé(s)" avec le détail des bougies reçues par
  // TF et les combinaisons ignorées.
  type LaunchFeedback =
    | { kind: 'fetching' }
    | { kind: 'ok'; nJobs: number; receivedBars?: Record<string, number>; fetchDetails?: Record<string, number>; skipped?: any[] }
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
    const d = jobsData as any;
    if (!d) return [];
    if (Array.isArray(d)) return d;
    if (Array.isArray(d.jobs)) return d.jobs;
    // Forme « job unique » : la réponse porte directement `job_id`
    // (appel `/api/optimize/status?job_id=…`).
    if (typeof d.job_id === 'string') return [d];
    /*
      Sinon la réponse est le DICTIONNAIRE indexé par job_id renvoyé par
      `get_all_jobs()` (app/engine/auto_optimizer.py:208) — c'est d'ailleurs ce
      que documente déjà `OptimizeStatusSchema`.

      Le repli précédent (`return d ? [d] : []`) emballait le dictionnaire
      ENTIER comme s'il s'agissait d'un seul job : sans job en cours, `{}` est
      truthy, donc la page rendait une carte fantôme au `job_id` undefined
      (d'où l'avertissement React « unique key prop »), et avec des jobs réels
      elle en affichait un seul, illisible. Même famille que R15/R16.
    */
    return Object.entries(d).map(([job_id, job]: [string, any]) => ({
      job_id,
      ...(job && typeof job === 'object' ? job : {}),
    })) as OptimizeJob[];
  })();
  const jobsError = jobsIsError
    ? (jobsErrorObj as any)?.message || 'Erreur de chargement'
    : null;

  // Sync default strategies selection once spaces load
  useEffect(() => {
    if (visibleStrategies.length > 0 && selectedStrategies.length === 0) {
      setSelectedStrategies(visibleStrategies.slice(0, 1));
    }
  }, [visibleStrategies, selectedStrategies.length]);

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
    for (const j of jobs) {
      const g = map[j.status] ?? map.done;
      g.jobs.push(j);
    }
    return groups.filter((g) => g.jobs.length > 0);
  }, [jobs]);
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

          {/* Symbols */}
          <div>
            <div className="text-xs text-dim mb-2">Symboles</div>
            <div className="flex flex-wrap gap-2">
              {ALL_SYMBOLS.map((sym) => {
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
                onChange={(e) => setNTrials(Math.max(1, Number(e.target.value) || 1))}
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
                onChange={(e) => setNJobs(Math.max(1, Number(e.target.value) || 1))}
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

          {/* OPT-004 — options avancées : early_stopping, limit_per_tf, ml_tune_hp. */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 pt-2 border-t border-border">
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
          "Tout ouvrir / Réduire tout" toggle. */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">Jobs</h2>
          <div className="flex items-center gap-3">
            {jobs.length > 0 && (
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
            <span className="text-xs text-dim font-mono">{jobs.length}</span>
          </div>
        </div>
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
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
