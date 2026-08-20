'use client';

/**
 * BT-005 — Card de progression pour le backtest.
 *
 * Extrait du template Jinja2 `backtest.html:163-168` (HTML) +
 * `backtest.html:373-398` (JS `progInterval` + steps simulés).
 *
 * Barre animée + ETA + log horodaté avec steps simulés.
 */

import { useEffect, useRef, useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Loader2, X } from 'lucide-react';

interface LogEntry {
  ts: string;
  level: 'run' | 'ok' | 'warn' | 'err';
  msg: string;
}

interface Props {
  startedAt: number;
  strategies: string[];
  walkForward: boolean;
  monteCarlo: boolean;
  onCancel: () => void;
  onComplete?: (durationSec: number) => void;
}

const BASE_STEPS = [
  'Connexion exchange & initialisation…',
  'Téléchargement des données OHLCV…',
  'Données reçues — calcul des indicateurs…',
  'Calcul des métriques & statistiques…',
  'Finalisation des résultats de base…',
  'Assemblage final & sérialisation JSON…',
];

function buildSteps(strategies: string[], wf: boolean, mc: boolean): string[] {
  const steps = [...BASE_STEPS.slice(0, 3)];
  strategies.forEach((s) => steps.push(`Exécution ${s}…`));
  steps.push(...BASE_STEPS.slice(3, 5));
  strategies.forEach((s) => steps.push(`↳ ${s} : backtest IS/OOS en cours…`));
  if (wf) {
    steps.push('Walk-Forward : découpage des folds…');
    strategies.forEach((s, i) => steps.push(`↳ WF fold ${Math.min(i + 1, 5)}/5 · ${s}…`));
  }
  if (mc) {
    steps.push('Monte-Carlo : simulation des scénarios…');
    steps.push('↳ MC runs 0-50 / 200…');
    steps.push('↳ MC runs 50-150 / 200…');
    steps.push('↳ MC runs 150-200 / 200…');
    steps.push('Monte-Carlo : calcul des percentiles P5/P50/P95…');
  }
  steps.push(BASE_STEPS[5]);
  return steps;
}

function ts(): string {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}

function etaText(elapsedSec: number, progressPct: number): string | null {
  if (elapsedSec < 5 || progressPct < 5) return null;
  const remaining = ((100 - progressPct) * elapsedSec) / progressPct;
  if (remaining < 60) return `~${Math.ceil(remaining)}s restant`;
  return `~${Math.ceil(remaining / 60)}min restant`;
}

export function BacktestProgress({ startedAt, strategies, walkForward, monteCarlo, onCancel, onComplete }: Props) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [progress, setProgress] = useState(0);
  const [eta, setEta] = useState<string | null>(null);
  const stepIdxRef = useRef(0);
  const logRef = useRef<HTMLDivElement>(null);

  const steps = buildSteps(strategies, walkForward, monteCarlo);

  useEffect(() => {
    const interval = setInterval(() => {
      const elapsed = (Date.now() - startedAt) / 1000;
      const nextIdx = Math.min(steps.length, Math.floor((elapsed / Math.max(15, steps.length * 1.5)) * steps.length));
      const newProgress = Math.min(95, ((nextIdx + 1) / steps.length) * 100);

      setProgress(newProgress);
      setEta(etaText(elapsed, newProgress));

      // Pousse les logs en retard jusqu'à nextIdx.
      while (stepIdxRef.current < nextIdx && stepIdxRef.current < steps.length) {
        const msg = steps[stepIdxRef.current];
        setLogs((prev) => [...prev, { ts: ts(), level: 'run', msg }]);
        stepIdxRef.current++;
      }

      // Si on est à 95% depuis > 2× l'ETA initial, warning.
      if (newProgress >= 95 && elapsed > 30 && eta && elapsed > 2 * parseInt(eta)) {
        // (garde-fou silencieux — pas d'UX supplémentaire pour l'instant)
      }
    }, 400);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startedAt, strategies.join(','), walkForward, monteCarlo]);

  // Auto-scroll du log panel vers le bas.
  useEffect(() => {
    if (logRef.current) {
      const el = logRef.current;
      const isAtBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 50;
      if (isAtBottom) el.scrollTop = el.scrollHeight;
    }
  }, [logs]);

  const handleComplete = () => {
    const duration = (Date.now() - startedAt) / 1000;
    setLogs((prev) => [
      ...prev,
      { ts: ts(), level: 'ok', msg: `✓ Backtest terminé en ${duration.toFixed(1)}s` },
    ]);
    setProgress(100);
    setEta(null);
    onComplete?.(duration);
  };

  return (
    <Card className="border-cyan-500/30 bg-cyan-500/5">
      <CardContent className="space-y-3 pt-4">
        <div className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-cyan-400" />
          <span className="text-sm font-medium">Backtest en cours…</span>
          <span className="text-xs text-muted-foreground font-mono ml-auto">{progress.toFixed(0)}%</span>
          <Button variant="ghost" size="sm" onClick={onCancel} className="h-7 px-2 text-xs">
            <X className="h-3 w-3 mr-1" /> Annuler
          </Button>
        </div>

        {/* Barre de progression */}
        <div className="space-y-1">
          <div className="h-1.5 bg-border rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-cyan-500 to-purple-500 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          {eta && (
            <div className="text-[0.65rem] text-muted-foreground font-mono">{eta}</div>
          )}
        </div>

        {/* Log panel */}
        <div
          ref={logRef}
          className="max-h-[200px] overflow-y-auto bg-surface border border-border rounded-md p-2 font-mono text-[0.7rem] space-y-0.5"
        >
          {logs.length === 0 && (
            <div className="text-muted-foreground italic">Initialisation…</div>
          )}
          {logs.map((l, i) => (
            <div key={i} className="flex gap-2">
              <span className="text-muted-foreground">{l.ts}</span>
              <span
                className={
                  l.level === 'ok' ? 'text-emerald-400' : l.level === 'warn' ? 'text-amber-400' : l.level === 'err' ? 'text-rose-400' : 'text-cyan-400'
                }
              >
                · {l.level.toUpperCase()} ·
              </span>
              <span className="text-foreground">{l.msg}</span>
            </div>
          ))}
        </div>

        {/* Bouton interne pour marquer complétion (utile pour tests) */}
        <button
          type="button"
          onClick={handleComplete}
          className="hidden"
          aria-hidden="true"
          data-testid="bt-progress-complete"
        />
      </CardContent>
    </Card>
  );
}
