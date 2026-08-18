'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { formatUSD } from '@/lib/utils';
import { toast } from 'sonner';
import {
  useApplyOptimize,
  useCancelOptimize,
  useDeleteOptimizeJob,
} from '@/hooks/use-api';
import {
  CheckCircle2, Trash2, StopCircle, ChevronDown, ChevronRight, AlertTriangle, Cpu,
} from 'lucide-react';
import type { OptimizeJob } from '@/types';
import { CostModelCard } from '@/components/cards/cost-model-card';
import { BeforeAfterGrid } from '@/components/cards/before-after-grid';
import { TopTrialsTable } from '@/components/tables/top-trials-table';
import { OptimizerWarnings } from '@/components/cards/optimizer-warnings';
import { TrialsChart } from '@/components/charts/trials-chart';
import { OptimizerValidatePanel } from '@/components/cards/optimizer-validate-panel';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { timeAgoShort, Metric } from '@/components/optimizer/optimizer-utils';
import { normalizeBaseline, deriveAfter, normalizeTopTrials } from '@/lib/backend-normalizers';
import { LiveProgress } from '@/components/optimizer/live-progress';
import { STATUS_LABEL, STATUS_VARIANT } from '@/components/optimizer/status';

export function JobCard({
  job,
  defaultExpanded,
  filterMl = false,
  compareMode = false,
  compareSelected = false,
  onCompareToggle,
}: {
  job: OptimizeJob;
  defaultExpanded?: boolean;
  filterMl?: boolean;
  /** P1-10 : mode comparaison — affiche une checkbox au lieu du bouton expand. */
  compareMode?: boolean;
  compareSelected?: boolean;
  onCompareToggle?: (jobId: string) => void;
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
      const applied = await apply.mutateAsync({ jobId: job.job_id });
      const src = applied?.gate_source;
      toast.success(
        src === 'holdout'
          ? 'Paramètres appliqués (gate holdout)'
          : src === 'selection'
            ? 'Paramètres appliqués (gate sur la tranche de sélection)'
            : 'Paramètres appliqués au slot',
      );
      // ML-004 — en mode ML, l'apply déclenche aussi l'entraînement du modèle
      // côté backend. On invalide les queries consommées par le tab ML pour
      // que la StrategyTable (statut Entraîné/Non entraîné, AUC) se
      // rafraîchisse sans attendre le prochain poll de 30 s.
      if (filterMl) {
        qc.invalidateQueries({ queryKey: ['mlInfo'] });
        qc.invalidateQueries({ queryKey: ['ml-recipes'] });
      }
    } catch (e: any) {
      // P1-8 : si le gate refuse (409), afficher un ConfirmDialog avec la
      // raison et un bouton "Forcer l'application" (force=true).
      const msg = e?.message ?? '';
      if (msg.includes('409') || msg.includes('refusé') || msg.includes('refused')) {
        setForceApplyDialog({ jobId: job.job_id, reason: msg, strategy: job.strategy, tf: job.timeframe });
      } else {
        toast.error(`Apply failed: ${msg}`);
      }
    }
  };

  // P1-8 : état du dialog "Forcer l'application" (quand le gate refuse)
  const [forceApplyDialog, setForceApplyDialog] = useState<{ jobId: string; reason: string; strategy: string; tf: string } | null>(null);

  const handleForceApply = async () => {
    if (!forceApplyDialog) return;
    try {
      await apply.mutateAsync({ jobId: forceApplyDialog.jobId, force: true });
      toast.success('Paramètres forcés au slot (override gate)');
      if (filterMl) {
        qc.invalidateQueries({ queryKey: ['mlInfo'] });
        qc.invalidateQueries({ queryKey: ['ml-recipes'] });
      }
    } catch (e: any) {
      toast.error(`Force apply failed: ${e.message}`);
    } finally {
      setForceApplyDialog(null);
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
  const baselineRec = job.baseline as { baseline_source?: string; source?: string } | undefined;
  const baselineSource = baselineRec?.baseline_source ?? baselineRec?.source;

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
              {/* P2-8 : badge si TF non recommandé */}
              {job.is_recommended === false && (
                <span className="text-amber-400 ml-2" title={`TFs recommandés: ${job.recommended_tfs?.join(', ') ?? '?'}`}>
                  ⚠ TF non recommandé
                </span>
              )}
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
                  Résultat validation
                  <span className="font-normal text-dim">
                    (tranche de sélection — pas un holdout)
                  </span>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                  <Metric label="Score val." value={(result.best_val_score ?? result.best_oos_score ?? 0).toFixed(4)} />
                  <Metric label="PnL val." value={formatUSD(result.best_val_pnl ?? result.best_oos_pnl ?? 0)} />
                  <Metric label="Trades val." value={String(result.best_val_trades ?? result.best_oos_trades ?? 0)} />
                  <Metric label="Win rate val." value={`${((result.best_val_wr ?? result.best_oos_wr ?? 0) * 100).toFixed(1)}%`} />
                  <Metric label="Sharpe val." value={(result.best_val_sharpe ?? result.best_oos_sharpe ?? 0).toFixed(2)} />
                  {/* P0-2 : Deflated Sharpe (probabilité que le Sharpe soit réel,
                      corrigée du biais de sélection multiple). */}
                  <Metric
                    label="Deflated Sharpe"
                    value={job.deflated_sharpe != null
                      ? `${(job.deflated_sharpe * 100).toFixed(1)}%`
                      : '—'}
                  />
                  {/* P0-3 : Walk-Forward consistency (% de folds OOS positifs
                      avec les best_params figés). */}
                  <Metric
                    label="WF Consistency"
                    value={job.wf_consistency != null
                      ? `${job.wf_consistency.toFixed(0)}%`
                      : '—'}
                  />
                  <Metric label="Apply" value={job.applied ? 'Oui' : 'Non'} />
                  {job.gate_source && (
                    <Metric
                      label="Gate"
                      value={job.gate_source === 'holdout' ? 'holdout' : 'sélection'}
                    />
                  )}
                </div>
                {/* P0-2 : warning si Deflated Sharpe < 50% (edge probablement nul) */}
                {job.deflated_sharpe != null && job.deflated_sharpe < 0.5 && (
                  <div className="flex items-center gap-1.5 text-[10px] text-amber-400">
                    <AlertTriangle className="w-3 h-3" />
                    Deflated Sharpe &lt; 50% — edge probablement nul (biais de sélection)
                  </div>
                )}
                {/* P0-3 : warning si WF consistency < 60% (params non robustes) */}
                {job.wf_consistency != null && job.wf_consistency < 60 && (
                  <div className="flex items-center gap-1.5 text-[10px] text-amber-400">
                    <AlertTriangle className="w-3 h-3" />
                    WF Consistency &lt; 60% — best_params non robustes sur fenêtres glissantes
                  </div>
                )}
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

            {/* P1-3 — Courbe d'apprentissage (final_score + overfit au fil des trials).
                Affichée seulement si ≥ 3 trials (sinon pas lisible). */}
            {isDone && trials.length >= 3 && (
              <TrialsChart trials={trials} />
            )}

            {/* Contexte facturé pendant l'optimisation : un `oos_score` n'est pas
                comparable d'un run à l'autre sans lui — deux scores très différents
                peuvent ne différer que par la venue résolue (spot vs margin). */}
            {isDone && result.cost_model && <CostModelCard model={result.cost_model} />}

            {/* P1-4 : éprouver le paramétrage retenu (Monte-Carlo, régimes).
                Réservé aux jobs terminés — la route exige un `best_params`. */}
            {isDone && result.best_params && (
              <OptimizerValidatePanel jobId={job.job_id} />
            )}

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

      {/* P1-8 : ConfirmDialog "Forcer l'application" quand le gate refuse.
          Affiche la raison du refus (Deflated Sharpe, échantillon insuffisant,
          PnL non amélioré) et propose un bouton "Forcer" (force=true). */}
      <ConfirmDialog
        open={!!forceApplyDialog}
        onOpenChange={(open) => { if (!open) setForceApplyDialog(null); }}
        title="⚠ Application refusée par le gate"
        description={
          forceApplyDialog
            ? `Le gate de qualité a refusé l'application des paramètres pour ${forceApplyDialog.strategy}@${forceApplyDialog.tf}. ` +
              `Raison : ${forceApplyDialog.reason}. ` +
              `Forcer l'application outrepasse le gate (Deflated Sharpe, échantillon minimum, PnL vs baseline). ` +
              `À utiliser en connaissance de cause — le paramétrage refusé deviendra actif.`
            : ''
        }
        confirmLabel="Forcer l'application"
        cancelLabel="Annuler"
        variant="danger"
        onConfirm={handleForceApply}
      />
    </Card>
  );
}
