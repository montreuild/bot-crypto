'use client';

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import {
  useStartMLTrain, useMLTrainStatus, useStartMLSweep, useMLSweepStatus,
  useConfig,
} from '@/hooks/use-api';
import { Loader2, Rocket, BarChart3 } from 'lucide-react';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { DiagnosticsPanel } from '@/components/models/ml-badges-and-diagnostics';
import type { MLJobStatus, ModelTrainMeta } from '@/types';

export function StrategySelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data: config } = useConfig();
  const strategies: string[] = config?.all_strategies ?? [];
  const options = strategies.includes(value) || !strategies.length
    ? (strategies.length ? strategies : [value])
    : [value, ...strategies];
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-full font-mono" aria-label="Recette">
        <SelectValue placeholder="Sélectionner une recette" />
      </SelectTrigger>
      <SelectContent>
        {options.map((s) => (<SelectItem key={s} value={s}>{s}</SelectItem>))}
      </SelectContent>
    </Select>
  );
}

export function TrainForm() {
  const qc = useQueryClient();
  const startTrain = useStartMLTrain();
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useMLTrainStatus(jobId);
  const [strategy, setStrategy] = useState('opus_omnibus_v11');
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [tf, setTf] = useState('1h');
  const [windowBars, setWindowBars] = useState(0);
  const [asOf, setAsOf] = useState('');
  const [publish, setPublish] = useState(false);

  useEffect(() => {
    if (job?.status === 'done') qc.invalidateQueries({ queryKey: ['mlRegistry'] });
  }, [job?.status, qc]);

  const handleSubmit = async () => {
    if (!strategy.trim() || !symbol.trim() || !tf.trim()) {
      toast.error('Recette/symbole/TF requis'); return;
    }
    try {
      const res = await startTrain.mutateAsync({
        strategy: strategy.trim(), symbol: symbol.trim(), tf: tf.trim(),
        window_bars: windowBars > 0 ? windowBars : null,
        as_of: asOf.trim() || null, publish,
      });
      setJobId(res.job_id);
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Entraîner un modèle</CardTitle>
        <Rocket className="w-4 h-4 text-primary-400" />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div>
            <label className="text-xs text-dim block mb-1.5">Recette</label>
            <StrategySelect value={strategy} onChange={setStrategy} />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Symbole</label>
            <input aria-label="Symbole" value={symbol} onChange={(e) => setSymbol(e.target.value)}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono" />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Timeframe</label>
            <TimeframeButtons value={tf} onChange={setTf} />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">
              Fenêtre (barres)<span className="block normal-case text-[10px] text-dim font-normal">0 = tout dispo</span>
            </label>
            <input aria-label="Fenêtre (barres)" type="number" min={0} value={windowBars}
              onChange={(e) => setWindowBars(Math.max(0, Number(e.target.value) || 0))}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono" />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">
              Date de fin d&apos;entraînement (optionnel)<span className="block normal-case text-[10px] text-dim font-normal">fige l&apos;entraînement à cette date passée</span>
            </label>
            <input aria-label="Entraîner comme au (as-of)" value={asOf} onChange={(e) => setAsOf(e.target.value)}
              placeholder="2026-06-01T00:00:00"
              title="Ne garde que les bougies antérieures à cette date. Vide = tout l'historique."
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono" />
          </div>
        </div>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={publish} onChange={(e) => setPublish(e.target.checked)} className="rounded" />
            Publier réellement (sinon dry-run)
          </label>
          <Button onClick={handleSubmit} disabled={startTrain.isPending} variant="primary">
            {startTrain.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
            Entraîner
          </Button>
        </div>
        <p className="text-xs text-muted">
          Dry-run (par défaut) : entraîne un candidat, le compare au sortant sur un holdout, n&apos;écrit rien.
          « Publier » déclenche le gate réel — mêmes règles qu&apos;en live.
        </p>
        {job && <JobResultInline job={job} />}
      </CardContent>
    </Card>
  );
}

export function SweepForm() {
  const qc = useQueryClient();
  const startSweep = useStartMLSweep();
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useMLSweepStatus(jobId);
  const [strategy, setStrategy] = useState('opus_omnibus_v11');
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [tf, setTf] = useState('1h');
  const [windowsStr, setWindowsStr] = useState('10000,20000,40000');
  const [publishBest, setPublishBest] = useState(false);
  const [asOf, setAsOf] = useState('');

  useEffect(() => {
    if (job?.status === 'done') qc.invalidateQueries({ queryKey: ['mlRegistry'] });
  }, [job?.status, qc]);

  const handleSubmit = async () => {
    const windows = windowsStr.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => n > 0);
    if (!strategy.trim() || !symbol.trim() || !tf.trim() || windows.length === 0) {
      toast.error('Recette/symbole/TF/fenêtres requis'); return;
    }
    try {
      const res = await startSweep.mutateAsync({
        strategy: strategy.trim(), symbol: symbol.trim(), tf: tf.trim(),
        windows, publish_best: publishBest, as_of: asOf.trim() || null,
      });
      setJobId(res.job_id);
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Window sweep</CardTitle>
        <BarChart3 className="w-4 h-4 text-primary-400" />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="text-xs text-dim block mb-1.5">Recette</label>
            <StrategySelect value={strategy} onChange={setStrategy} />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Symbole</label>
            <input aria-label="Symbole" value={symbol} onChange={(e) => setSymbol(e.target.value)}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono" />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Timeframe</label>
            <TimeframeButtons value={tf} onChange={setTf} />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Fenêtres (barres, CSV)</label>
            <input aria-label="Fenêtres (barres, CSV)" value={windowsStr} onChange={(e) => setWindowsStr(e.target.value)}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono" />
          </div>
        </div>
        <div>
          <label className="text-xs text-dim block mb-1.5">
            As-of (optionnel)<span className="normal-case text-[10px] text-dim font-normal ml-1">Date ISO — vide = maintenant.</span>
          </label>
          <input aria-label="As-of" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)}
            className="w-full max-w-xs px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono" />
        </div>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={publishBest} onChange={(e) => setPublishBest(e.target.checked)} className="rounded" />
            Publier le meilleur (gaté)
          </label>
          <Button onClick={handleSubmit} disabled={startSweep.isPending} variant="primary">
            {startSweep.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <BarChart3 className="w-4 h-4" />}
            Comparer
          </Button>
        </div>
        <p className="text-xs text-muted">
          Compare chaque fenêtre sur un holdout COMMUN — comparaison légitime entre tailles. Sans « Publier », rien n&apos;est écrit.
        </p>
        {job && <JobResultInline job={job} />}
      </CardContent>
    </Card>
  );
}

// JobResult inline (utilise DiagnosticsPanel du module partagé)
function JobResultInline({ job }: { job: MLJobStatus }) {
  const isRunning = job.status === 'running';
  const isError = job.status === 'error';
  const res = job.result || {};
  return (
    <div className={cn(
      'rounded-lg border p-3 text-xs space-y-1',
      isRunning ? 'border-cyan-500/40 bg-cyan-500/5'
        : isError ? 'border-red-500/30 bg-red-500/5'
          : 'border-emerald-500/30 bg-emerald-500/5',
    )}>
      {isRunning && (
        <div className="flex items-center gap-2 text-muted">
          <Loader2 className="w-3.5 h-3.5 animate-spin text-cyan-400" />
          En cours… ({job.strategy}/{job.symbol}/{job.tf})
        </div>
      )}
      {isError && <div className="text-red-400">Échec : {job.error}</div>}
      {!isRunning && !isError && (
        <div className="space-y-1 text-muted">
          {res.decision && (
            <>
              <div><span className="font-semibold text-foreground">{res.decision}</span> — {res.reason || ''}</div>
              {res.candidate?.auc_amp != null && <div>Candidat : AUC amp = {Number(res.candidate.auc_amp).toFixed(3)}</div>}
              {res.published_version && <div>Version publiée : <span className="font-mono">{res.published_version}</span></div>}
            </>
          )}
          {res.candidates && (
            <>
              {res.candidates.map((c: any, i: number) => (
                <div key={i} className="font-mono">
                  {c.window_bars} barres : {c.auc_amp != null ? Number(c.auc_amp).toFixed(3) : (c.skipped || '—')}
                </div>
              ))}
              {res.best_window_bars && <div className="font-semibold text-foreground">Meilleure fenêtre : {res.best_window_bars}</div>}
              {res.published_version ? <div>Version publiée : <span className="font-mono">{res.published_version}</span></div> : res.note ? <div>{res.note}</div> : null}
            </>
          )}
          {!res.decision && !res.candidates && <div>Terminé.</div>}
          <DiagnosticsPanel trainMeta={res.train_meta as ModelTrainMeta | undefined} title="Diagnostics du candidat entraîné" />
        </div>
      )}
    </div>
  );
}
