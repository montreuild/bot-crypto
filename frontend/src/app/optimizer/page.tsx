'use client';

import { useEffect, useState } from 'react';
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
  Sparkles, Cpu, Layers, Activity, Zap,
} from 'lucide-react';
import type { OptimizeJob, OptimizeSpaces } from '@/types';

// ── Helpers ─────────────────────────────────────────────────────────────────

const METHODS = ['grid', 'random', 'bayesian'] as const;
const ALL_SYMBOLS = ['BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'BNB/USDC', 'XRP/USDC'];
const ALL_TFS = ['5m', '15m', '1h', '4h', '1d'];

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

  useEffect(() => {
    if (job.status !== 'running') {
      setLive(null);
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
        <span className="font-mono text-muted">{progress.toFixed(1)}%</span>
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

function JobCard({ job }: { job: OptimizeJob }) {
  const apply = useApplyOptimize();
  const cancel = useCancelOptimize();
  const del = useDeleteOptimizeJob();

  const handleApply = async () => {
    try {
      await apply.mutateAsync({ jobId: job.job_id });
      toast.success('Paramètres appliqués au slot');
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

  return (
    <Card className="space-y-3">
      <CardContent className="space-y-3">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Cpu className="w-4 h-4 text-primary-400 flex-shrink-0" />
              <span className="font-mono text-sm font-semibold truncate">{job.strategy}</span>
              <span className="text-xs text-muted">{job.timeframe}</span>
              {job.symbol && <span className="text-xs text-dim">· {job.symbol}</span>}
            </div>
            <div className="text-[10px] text-dim mt-1 font-mono">
              {job.job_id} · {job.method} · started {job.started_at ? timeAgoShort(job.started_at) : '—'}
            </div>
          </div>
          <Badge variant={variant}>{STATUS_LABEL[job.status] || job.status}</Badge>
        </div>

        {/* Progress */}
        <LiveProgress job={job} />

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

        {/* Error */}
        {job.status === 'error' && job.error && (
          <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded p-2">
            {job.error}
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-2 pt-1 border-t border-border">
          {isDone && !job.applied && (
            <Button
              size="sm"
              variant="success"
              onClick={handleApply}
              disabled={apply.isPending}
              className="flex-1"
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
            >
              <Trash2 className="w-3 h-3" />
            </Button>
          )}
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

// ── Main page ───────────────────────────────────────────────────────────────

export default function OptimizerPage() {
  const { data: spaces, isLoading: spacesLoading } = useOptimizeSpaces();
  const { data: resultsData } = useOptimizeResults();
  const startOptimize = useStartOptimize();

  // All known strategies from param space (fallback to defaults)
  const allStrategies = spaces ? Object.keys(spaces) : [];

  // Form state
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [selectedTfs, setSelectedTfs] = useState<string[]>(['1h']);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(['BTC/USDC']);
  const [method, setMethod] = useState<(typeof METHODS)[number]>('bayesian');
  const [nTrials, setNTrials] = useState(60);
  const [nJobs, setNJobs] = useState(1);
  const [autoApply, setAutoApply] = useState(false);

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
    if (Array.isArray(d?.jobs)) return d.jobs;
    if (Array.isArray(d)) return d;
    return d ? [d] : [];
  })();
  const jobsError = jobsIsError
    ? (jobsErrorObj as any)?.message || 'Erreur de chargement'
    : null;

  // Sync default strategies selection once spaces load
  useEffect(() => {
    if (allStrategies.length > 0 && selectedStrategies.length === 0) {
      setSelectedStrategies(allStrategies.slice(0, 1));
    }
  }, [allStrategies, selectedStrategies.length]);

  const toggle = (list: string[], value: string, setter: (v: string[]) => void) => {
    setter(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  };

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
    try {
      toast.info('Optimisation lancée...');
      await startOptimize.mutateAsync({
        strategies: selectedStrategies.join(','),
        timeframes: selectedTfs.join(','),
        symbols: selectedSymbols.join(','),
        method,
        n_trials: nTrials,
        n_jobs: nJobs,
        auto_apply: autoApply,
      });
      toast.success('Job démarré');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const activeResults = resultsData?.active_per_tf || {};

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Optimiseur</h1>
          <p className="text-sm text-muted mt-1">
            Optimisation bayésienne / grid / random des stratégies avec validation OOS
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
              <Layers className="w-3 h-3" /> Stratégies
            </div>
            <div className="flex flex-wrap gap-2">
              {(allStrategies.length > 0 ? allStrategies : ['pullback_trend', 'trend_rider', 'breakout', 'smart_money']).map((s) => {
                const active = selectedStrategies.includes(s);
                const isMl = spaces?.[s]?.is_ml;
                return (
                  <button
                    key={s}
                    onClick={() => toggle(selectedStrategies, s, setSelectedStrategies)}
                    className={cn(
                      'px-3 py-1.5 rounded-lg text-xs font-mono border transition-all',
                      active
                        ? 'bg-primary-500/15 text-primary-400 border-primary-500/40'
                        : 'bg-card-hover text-muted border-border hover:border-border-hi',
                    )}
                  >
                    {s}
                    {isMl && <span className="ml-1 text-purple-400">ML</span>}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Timeframes */}
          <div>
            <div className="text-xs text-dim mb-2">Timeframes</div>
            <div className="flex flex-wrap gap-2">
              {ALL_TFS.map((tf) => {
                const active = selectedTfs.includes(tf);
                return (
                  <button
                    key={tf}
                    onClick={() => toggle(selectedTfs, tf, setSelectedTfs)}
                    className={cn(
                      'px-3 py-1.5 rounded-lg text-xs font-mono border transition-all',
                      active
                        ? 'bg-purple-500/15 text-purple-400 border-purple-500/40'
                        : 'bg-card-hover text-muted border-border hover:border-border-hi',
                    )}
                  >
                    {tf}
                  </button>
                );
              })}
            </div>
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
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className="text-xs text-dim block mb-1.5">Méthode</label>
              <select
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
          </div>

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
            Lancer l'optimisation
          </Button>
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

      {/* Jobs list */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">Jobs</h2>
          <span className="text-xs text-dim font-mono">{jobs.length}</span>
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
              Aucun job d'optimisation. Lancez-en un ci-dessus.
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {jobs.map((job) => (
              <JobCard key={job.job_id} job={job} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
