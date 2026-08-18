'use client';

import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { OptimizeJob } from '@/types';

export function LiveProgress({ job }: { job: OptimizeJob }) {
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
    // P2-7 : reconnexion SSE avec backoff exponentiel (1s/2s/4s/8s/16s, max 5 essais).
    let retryCount = 0;
    const MAX_RETRIES = 5;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let es: EventSource | null = null;

    const connect = () => {
      es = new EventSource(url, { withCredentials: true });
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          setLive(data);
          retryCount = 0; // reset sur succès
          if (data.status === 'done' || data.status === 'error' || data.status === 'cancelled') {
            es?.close();
          }
        } catch {
          // ignore malformed SSE frames
        }
      };
      es.onerror = () => {
        es?.close();
        if (retryCount < MAX_RETRIES) {
          const delay = Math.pow(2, retryCount) * 1000; // 1s, 2s, 4s, 8s, 16s
          retryCount++;
          retryTimer = setTimeout(connect, delay);
        }
        // Si max retries atteint, le polling /status prend le relais
      };
    };
    connect();
    return () => {
      es?.close();
      if (retryTimer) clearTimeout(retryTimer);
    };
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

