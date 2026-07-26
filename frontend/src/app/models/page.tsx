'use client';

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn, formatDateTime } from '@/lib/utils';
import { toast } from 'sonner';
import {
  useMLRegistry, useMLRegistryVersions, useMLRegistryDecisions,
  usePinModel, useUnpinModel, usePromoteModel,
  useStartMLTrain, useMLTrainStatus, useStartMLSweep, useMLSweepStatus,
  useConfig,
} from '@/hooks/use-api';
import {
  Loader2, Database, AlertCircle, Pin, PinOff, ChevronDown, ChevronRight,
  Rocket, BarChart3, RefreshCw, ThumbsUp, ThumbsDown,
} from 'lucide-react';
import type {
  ModelRegistryEntry, ModelArtifact, ModelDecision, MLJobStatus,
  ModelFeatureImportance, ModelRegimeAuc, ModelTrainMeta,
  ModelRegimeFeatureImportance, ModelRegimeSimilarity,
} from '@/types';

// ── Badges ──────────────────────────────────────────────────────────────────

function AucBadge({ auc, trainEnd }: { auc: number | null | undefined; trainEnd?: string | null }) {
  // auc=0 sans date d'entraînement = non mesurée (artefact sans provenance
  // datée), PAS un modèle mesuré mauvais — distinct d'un vrai 0.000 (qui
  // aurait une date). Éviter le rouge trompeur dans les deux cas.
  if (auc == null || (auc === 0 && !trainEnd)) {
    return <Badge title="AUC non mesurée (artefact sans provenance datée)">n/m</Badge>;
  }
  const variant = auc >= 0.65 ? 'success' : auc >= 0.55 ? 'warning' : 'danger';
  return <Badge variant={variant}>{auc.toFixed(3)}</Badge>;
}

function DecisionBadge({ decision }: { decision: string | null | undefined }) {
  if (!decision) return <Badge>—</Badge>;
  const ok = decision === 'promote' || decision === 'initial' || decision === 'manual';
  return <Badge variant={ok ? 'success' : 'danger'}>{decision}</Badge>;
}

// ── Version row (historique + actions) ──────────────────────────────────────

function VersionRow({ entry, version }: { entry: ModelRegistryEntry; version: ModelArtifact }) {
  const pin = usePinModel();
  const unpin = useUnpinModel();
  const promote = usePromoteModel();
  const isPinned = entry.pinned_version_id === version.version_id;
  const busy = pin.isPending || unpin.isPending || promote.isPending;

  const handlePinToggle = async () => {
    try {
      if (isPinned) {
        await unpin.mutateAsync({ tf: entry.tf, recipe: entry.recipe });
        toast.success('Pin retiré');
      } else {
        await pin.mutateAsync({
          tf: entry.tf, recipe: entry.recipe, versionId: version.version_id,
        });
        toast.success('Version épinglée');
      }
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  const handlePromote = async (decision: 'manual' | 'keep') => {
    const verb = decision === 'manual' ? 'Promouvoir' : 'Rejeter';
    if (!window.confirm(`${verb} la version ${version.version_id} pour ${entry.tf}/${entry.recipe} ?`)) {
      return;
    }
    try {
      await promote.mutateAsync({
        tf: entry.tf, recipe: entry.recipe,
        versionId: version.version_id, decision,
      });
      toast.success('Décision mise à jour');
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  return (
    <tr className="border-b border-border/20">
      <td className="p-2 font-mono">{version.version_id}</td>
      <td className="p-2 text-muted font-mono">{formatDateTime(version.train_end)}</td>
      <td className="p-2 text-right font-mono">{version.n_bars ?? '—'}</td>
      <td className="p-2"><AucBadge auc={version.auc} trainEnd={version.train_end} /></td>
      <td className="p-2"><DecisionBadge decision={version.gate_decision} /></td>
      <td className="p-2">
        <div className="flex flex-wrap gap-1.5">
          <Button
            size="sm" variant="outline" onClick={handlePinToggle} disabled={busy}
            className={isPinned ? 'text-purple-400 border-purple-500/40 bg-purple-500/10' : ''}
          >
            {isPinned ? <PinOff className="w-3 h-3" /> : <Pin className="w-3 h-3" />}
            {isPinned ? 'Retirer' : 'Épingler'}
          </Button>
          <Button size="sm" variant="success" onClick={() => handlePromote('manual')} disabled={busy}>
            <ThumbsUp className="w-3 h-3" />
            Promouvoir
          </Button>
          <Button size="sm" variant="danger" onClick={() => handlePromote('keep')} disabled={busy}>
            <ThumbsDown className="w-3 h-3" />
            Rejeter
          </Button>
        </div>
      </td>
    </tr>
  );
}

function DecisionsTable({ decisions }: { decisions: ModelDecision[] }) {
  if (decisions.length === 0) {
    return <div className="text-xs text-dim">Aucune décision journalisée.</div>;
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-dim border-b border-border/50">
          <th className="p-2 font-medium">Date</th>
          <th className="p-2 font-medium">Version</th>
          <th className="p-2 font-medium">Décision</th>
          <th className="p-2 font-medium">Source</th>
          <th className="p-2 font-medium">Raison</th>
        </tr>
      </thead>
      <tbody>
        {decisions.map((d, i) => (
          <tr key={`${d.ts}-${d.version_id}-${i}`} className="border-b border-border/20">
            <td className="p-2 font-mono text-muted">{formatDateTime(d.ts)}</td>
            <td className="p-2 font-mono">{d.version_id}</td>
            <td className="p-2"><DecisionBadge decision={d.decision} /></td>
            <td className="p-2 text-dim">{d.source || '—'}</td>
            <td className="p-2 text-muted">{d.reason || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Diagnostics (version active) : top features + AUC direction par régime ──

const REGIME_LABELS: Record<string, string> = {
  range: 'Range', trend_up: 'Trend Up', trend_down: 'Trend Down', choppy: 'Choppy',
};

function FeatureImportanceTable({ items }: { items?: ModelFeatureImportance[] }) {
  if (!items || items.length === 0) {
    return <div className="text-xs text-dim">Non disponible.</div>;
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-dim border-b border-border/50">
          <th className="p-2 font-medium">Feature</th>
          <th className="p-2 font-medium text-right">Importance (gain)</th>
        </tr>
      </thead>
      <tbody>
        {items.map((f, i) => (
          <tr key={`${f.feature}-${i}`} className="border-b border-border/20">
            <td className="p-2 font-mono">{f.feature}</td>
            <td className="p-2 text-right font-mono">{f.gain}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RegimeAucTable({ byRegime }: { byRegime?: Record<string, ModelRegimeAuc> }) {
  const keys = Object.keys(byRegime || {}).filter((k) => REGIME_LABELS[k]);
  if (keys.length === 0) {
    return <div className="text-xs text-dim">Non disponible.</div>;
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-dim border-b border-border/50">
          <th className="p-2 font-medium">Régime</th>
          <th className="p-2 font-medium">N</th>
          <th className="p-2 font-medium">AUC direction</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const v = (byRegime || {})[k] || { n: null, auc: null };
          return (
            <tr key={k} className="border-b border-border/20">
              <td className="p-2">{REGIME_LABELS[k]}</td>
              <td className="p-2 font-mono text-muted">
                {v.n != null ? v.n : v.approx_n != null ? `~${v.approx_n} (population régime)` : '—'}
              </td>
              <td className="p-2">
                {v.auc != null ? <AucBadge auc={v.auc} trainEnd="x" /> : <Badge>n/a</Badge>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Importance des features PAR RÉGIME (attributions `pred_contrib`) — permet de
 *  voir si le modèle direction s'appuie sur des signaux différents selon le
 *  régime de marché, ce que l'importance « gain » globale ne peut pas dire. */
function RegimeFeatureColumns({ byRegime }: { byRegime?: Record<string, ModelRegimeFeatureImportance> }) {
  const keys = Object.keys(byRegime || {}).filter(
    (k) => REGIME_LABELS[k] && ((byRegime || {})[k].top || []).length > 0,
  );
  if (keys.length === 0) return null;
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {keys.map((k) => {
        const v = (byRegime || {})[k];
        return (
          <div key={k}>
            <div className="text-xs text-muted mb-1.5">
              {REGIME_LABELS[k]} <span className="text-dim">(n={v.n})</span>
            </div>
            <table className="w-full text-xs">
              <tbody>
                {v.top.slice(0, 8).map((f, i) => (
                  <tr key={`${f.feature}-${i}`} className="border-b border-border/20">
                    <td className="p-1.5 font-mono">{f.feature}</td>
                    <td className="p-1.5 text-right font-mono text-muted">{f.contrib}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

/** Similarité des importances entre régimes : Spearman ≈ 1 signifie que le
 *  modèle hiérarchise les features de la même façon partout (donc aucune
 *  spécialisation par régime à exploiter). */
function RegimeSimilarityTable({ sim }: { sim?: Record<string, ModelRegimeSimilarity> }) {
  const keys = Object.keys(sim || {});
  if (keys.length === 0) return null;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-dim border-b border-border/50">
          <th className="p-2 font-medium">Paire de régimes</th>
          <th className="p-2 font-medium">Spearman</th>
          <th className="p-2 font-medium">Overlap top</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const v = (sim || {})[k];
          const [a, b] = k.split('__vs__');
          const rho = v.spearman;
          const variant = rho == null ? 'default'
            : rho >= 0.9 ? 'danger' : rho >= 0.7 ? 'warning' : 'success';
          return (
            <tr key={k} className="border-b border-border/20">
              <td className="p-2">{(REGIME_LABELS[a] || a)} ↔ {(REGIME_LABELS[b] || b)}</td>
              <td className="p-2">
                {rho == null ? <Badge>n/a</Badge> : <Badge variant={variant}>{rho.toFixed(3)}</Badge>}
              </td>
              <td className="p-2 font-mono text-muted">
                {v.top_overlap != null ? `${(v.top_overlap * 100).toFixed(0)}%` : '—'}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function DiagnosticsPanel({ trainMeta }: { trainMeta?: ModelTrainMeta }) {
  if (!trainMeta || Object.keys(trainMeta).length === 0) return null;
  const hasFeats = (trainMeta.feature_importance_amp?.length || trainMeta.feature_importance_dir?.length);
  const hasRegime = trainMeta.auc_dir_by_regime && Object.keys(trainMeta.auc_dir_by_regime).length > 0;
  const fiReg = trainMeta.feature_importance_dir_by_regime;
  const hasFiReg = fiReg && Object.keys(fiReg).some((k) => (fiReg[k].top || []).length > 0);
  const sim = trainMeta.regime_feature_similarity;
  const hasSim = sim && Object.keys(sim).length > 0;
  if (!hasFeats && !hasRegime && !hasFiReg) return null;

  return (
    <div className="mt-4">
      <div className="text-[10px] uppercase tracking-wider text-dim font-semibold mb-2">
        Diagnostics (version active)
      </div>
      <div className="text-xs text-muted mb-3">
        {trainMeta.calibrated
          ? `Calibré ✓${trainMeta.cal_err ? ` — erreur moyenne : amp=${trainMeta.cal_err.amp ?? '—'} dir=${trainMeta.cal_err.dir ?? '—'}` : ''}`
          : 'Non calibré'}
        {trainMeta.n_features ? ` · ${trainMeta.n_features} features` : ''}
        {trainMeta.horizons ? ` · horizons=${JSON.stringify(trainMeta.horizons)}` : ''}
      </div>
      {hasRegime && (
        <div className="mb-4">
          <div className="text-xs text-muted mb-1.5">AUC direction par régime</div>
          <RegimeAucTable byRegime={trainMeta.auc_dir_by_regime} />
        </div>
      )}
      {hasFiReg && (
        <div className="mb-4">
          <div className="text-xs text-muted mb-1.5">
            Top features direction PAR RÉGIME{' '}
            <span className="text-dim">(attributions moyennes |contribution|)</span>
          </div>
          <RegimeFeatureColumns byRegime={fiReg} />
        </div>
      )}
      {hasSim && (
        <div className="mb-4">
          <div className="text-xs text-muted mb-1.5">Similarité des importances entre régimes</div>
          <RegimeSimilarityTable sim={sim} />
          <p className="text-xs text-dim mt-1.5">
            Spearman ≈ 1 = le modèle classe les features de la même façon dans les deux régimes
            (pas de spécialisation exploitable). Un écart net serait la piste d'un routing
            conditionnel au régime.
          </p>
        </div>
      )}
      {hasFeats && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="text-xs text-muted mb-1.5">Top features — amplitude</div>
            <FeatureImportanceTable items={trainMeta.feature_importance_amp} />
          </div>
          <div>
            <div className="text-xs text-muted mb-1.5">Top features — direction</div>
            <FeatureImportanceTable items={trainMeta.feature_importance_dir} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Registry row (recette + détail dépliable) ───────────────────────────────

function RegistryRow({ entry }: { entry: ModelRegistryEntry }) {
  const [expanded, setExpanded] = useState(false);
  const { data: versionsData, isLoading: versionsLoading } = useMLRegistryVersions(
    expanded ? entry.tf : null, expanded ? entry.recipe : null,
  );
  const { data: decisionsData } = useMLRegistryDecisions(
    expanded ? entry.tf : null, expanded ? entry.recipe : null,
  );

  const active = entry.active;

  return (
    <>
      <tr className="border-b border-border/30 hover:bg-card-hover">
        <td className="p-3"><Badge variant="purple">{entry.tf}</Badge></td>
        <td className="p-3 font-mono text-xs">{entry.recipe}</td>
        <td className="p-3 font-mono text-xs text-muted">{entry.train_symbol ?? '—'}</td>
        <td className="p-3 font-mono text-xs">
          {active ? active.version_id : <span className="text-dim">aucune</span>}
        </td>
        <td className="p-3 text-xs text-muted font-mono">
          {active ? formatDateTime(active.train_end) : '—'}
        </td>
        <td className="p-3">{active ? <AucBadge auc={active.auc} trainEnd={active.train_end} /> : '—'}</td>
        <td className="p-3">{active ? <DecisionBadge decision={active.gate_decision} /> : '—'}</td>
        <td className="p-3">
          <div className="flex flex-col gap-1 items-start">
            {entry.freshness_warning ? (
              <Badge variant="warning" title={entry.freshness_warning} className="whitespace-nowrap">
                <AlertCircle className="w-3 h-3" />
                {entry.freshness_warning.length > 28
                  ? `${entry.freshness_warning.slice(0, 28)}…`
                  : entry.freshness_warning}
              </Badge>
            ) : (
              <Badge variant="success">OK</Badge>
            )}
            {entry.pinned_version_id && (
              <Badge variant="purple" title={`Épinglé sur ${entry.pinned_version_id}`}>
                <Pin className="w-3 h-3" />
                pin
              </Badge>
            )}
          </div>
        </td>
        <td className="p-3 text-right font-mono">{entry.n_versions}</td>
        <td className="p-3">
          <Button size="sm" variant="ghost" onClick={() => setExpanded((v) => !v)}>
            {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            {expanded ? 'Masquer' : 'Détails'}
          </Button>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-black/10">
          <td colSpan={10} className="p-4">
            <div className="text-[10px] uppercase tracking-wider text-dim font-semibold mb-2">Versions</div>
            {versionsLoading ? (
              <div className="flex items-center py-4">
                <Loader2 className="w-4 h-4 animate-spin text-primary-400" />
              </div>
            ) : (
              <div className="overflow-x-auto mb-4">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-dim border-b border-border/50">
                      <th className="p-2 font-medium">Version</th>
                      <th className="p-2 font-medium">Entraînée jusqu'au</th>
                      <th className="p-2 font-medium text-right">N barres</th>
                      <th className="p-2 font-medium">AUC</th>
                      <th className="p-2 font-medium">Décision</th>
                      <th className="p-2 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(versionsData?.versions || []).map((v) => (
                      <VersionRow key={v.version_id} entry={entry} version={v} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="text-[10px] uppercase tracking-wider text-dim font-semibold mb-2">
              Décisions récentes
            </div>
            <DecisionsTable decisions={decisionsData?.decisions || []} />
            <DiagnosticsPanel trainMeta={entry.active?.train_meta} />
          </td>
        </tr>
      )}
    </>
  );
}

function RegistryTable({ entries }: { entries: ModelRegistryEntry[] }) {
  if (entries.length === 0) {
    return <div className="text-sm text-muted text-center py-6">Aucun modèle dans le registre.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-dim border-b border-border">
            <th className="p-3 font-medium">TF</th>
            <th className="p-3 font-medium">Recette</th>
            <th className="p-3 font-medium" title="Symbole dont proviennent les bougies d&apos;entraînement — le modèle sert tous les symboles tradés">
              Entraîné sur
            </th>
            <th className="p-3 font-medium">Version active</th>
            <th className="p-3 font-medium">Entraînée jusqu'au</th>
            <th className="p-3 font-medium">AUC</th>
            <th className="p-3 font-medium">Décision</th>
            <th className="p-3 font-medium">Fraîcheur</th>
            <th className="p-3 font-medium text-right">Versions</th>
            <th className="p-3 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <RegistryRow key={`${e.tf}|${e.recipe}`} entry={e} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Résultat de job asynchrone (entraînement / sweep) ───────────────────────

function JobResult({ job }: { job: MLJobStatus }) {
  const isRunning = job.status === 'running';
  const isError = job.status === 'error';
  const res = job.result || {};

  return (
    <div className={cn(
      'rounded-lg border p-3 text-xs space-y-1',
      isRunning ? 'border-cyan-500/40 bg-cyan-500/5'
        : isError ? 'border-red-500/30 bg-red-500/5'
          : 'border-emerald-500/30 bg-emerald-500/5',
    )}
    >
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
              {res.candidate?.auc_amp != null && (
                <div>Candidat : AUC amp = {Number(res.candidate.auc_amp).toFixed(3)}</div>
              )}
              {res.published_version && (
                <div>Version publiée : <span className="font-mono">{res.published_version}</span></div>
              )}
            </>
          )}
          {res.candidates && (
            <>
              {res.candidates.map((c: any, i: number) => (
                <div key={i} className="font-mono">
                  {c.window_bars} barres : {c.auc_amp != null ? Number(c.auc_amp).toFixed(3) : (c.skipped || '—')}
                </div>
              ))}
              {res.best_window_bars && (
                <div className="font-semibold text-foreground">Meilleure fenêtre : {res.best_window_bars}</div>
              )}
              {res.published_version ? (
                <div>Version publiée : <span className="font-mono">{res.published_version}</span></div>
              ) : res.note ? <div>{res.note}</div> : null}
            </>
          )}
          {!res.decision && !res.candidates && <div>Terminé.</div>}
        </div>
      )}
    </div>
  );
}

// ── Listes fermées : stratégies + timeframes ────────────────────────────────
// La table du registre est indexée par RECETTE (omnibus_v4_multi…) alors que
// l'entraînement attend une STRATÉGIE (opus_omnibus_v11…). Recopier un nom de
// recette dans un champ libre donnait « Stratégie inconnue » — et « 15min »
// pour « 15m » un job qui échouait plus tard sur un cache prétendument vide.
const TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d'];

const SELECT_CLASS =
  'w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono';

function StrategySelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data: config } = useConfig();
  const strategies: string[] = config?.all_strategies ?? [];
  // Garder la valeur courante sélectionnable tant que la config n'est pas
  // arrivée (ou si elle ne la contient pas) : le select ne doit jamais se
  // vider sous les doigts de l'utilisateur.
  const options = strategies.includes(value) || !strategies.length
    ? (strategies.length ? strategies : [value])
    : [value, ...strategies];
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={SELECT_CLASS}>
      {options.map((s) => <option key={s} value={s}>{s}</option>)}
    </select>
  );
}

function TimeframeSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={SELECT_CLASS}>
      {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
    </select>
  );
}

// ── Formulaire d'entraînement ────────────────────────────────────────────────

function TrainForm() {
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
      toast.error('Stratégie/symbole/TF requis');
      return;
    }
    try {
      const res = await startTrain.mutateAsync({
        strategy: strategy.trim(), symbol: symbol.trim(), tf: tf.trim(),
        window_bars: windowBars > 0 ? windowBars : null,
        as_of: asOf.trim() || null,
        publish,
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
            <label className="text-xs text-dim block mb-1.5">Stratégie</label>
            <StrategySelect value={strategy} onChange={setStrategy} />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Symbole</label>
            <input
              value={symbol} onChange={(e) => setSymbol(e.target.value)}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Timeframe</label>
            <TimeframeSelect value={tf} onChange={setTf} />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">
              Fenêtre (barres)
              <span className="block normal-case text-[10px] text-dim font-normal">0 = tout dispo</span>
            </label>
            <input
              type="number" min={0} value={windowBars}
              onChange={(e) => setWindowBars(Math.max(0, Number(e.target.value) || 0))}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">
              Date de fin d'entraînement (optionnel)
              <span className="block normal-case text-[10px] text-dim font-normal">
                fige l'entraînement à cette date passée
              </span>
            </label>
            <input
              value={asOf} onChange={(e) => setAsOf(e.target.value)}
              placeholder="2026-06-01T00:00:00"
              title="Ne garde que les bougies antérieures à cette date pour l'entraînement ET le holdout du gate. Vide = tout l'historique disponible jusqu'à maintenant."
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            />
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
          Dry-run (par défaut) : entraîne un candidat, le compare au sortant sur un holdout, n'écrit rien.
          « Publier » déclenche le gate réel (promotion seulement si le candidat ne régresse pas) — mêmes
          règles qu'en live.
        </p>
        {job && <JobResult job={job} />}
      </CardContent>
    </Card>
  );
}

// ── Formulaire de window sweep ───────────────────────────────────────────────

function SweepForm() {
  const qc = useQueryClient();
  const startSweep = useStartMLSweep();
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useMLSweepStatus(jobId);

  const [strategy, setStrategy] = useState('opus_omnibus_v11');
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [tf, setTf] = useState('1h');
  const [windowsStr, setWindowsStr] = useState('10000,20000,40000');
  const [publishBest, setPublishBest] = useState(false);

  useEffect(() => {
    if (job?.status === 'done') qc.invalidateQueries({ queryKey: ['mlRegistry'] });
  }, [job?.status, qc]);

  const handleSubmit = async () => {
    const windows = windowsStr.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => n > 0);
    if (!strategy.trim() || !symbol.trim() || !tf.trim() || windows.length === 0) {
      toast.error('Stratégie/symbole/TF/fenêtres requis');
      return;
    }
    try {
      const res = await startSweep.mutateAsync({
        strategy: strategy.trim(), symbol: symbol.trim(), tf: tf.trim(),
        windows, publish_best: publishBest,
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
            <label className="text-xs text-dim block mb-1.5">Stratégie</label>
            <StrategySelect value={strategy} onChange={setStrategy} />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Symbole</label>
            <input
              value={symbol} onChange={(e) => setSymbol(e.target.value)}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Timeframe</label>
            <TimeframeSelect value={tf} onChange={setTf} />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Fenêtres (barres, CSV)</label>
            <input
              value={windowsStr} onChange={(e) => setWindowsStr(e.target.value)}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            />
          </div>
        </div>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox" checked={publishBest} onChange={(e) => setPublishBest(e.target.checked)}
              className="rounded"
            />
            Publier le meilleur (gaté)
          </label>
          <Button onClick={handleSubmit} disabled={startSweep.isPending} variant="primary">
            {startSweep.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <BarChart3 className="w-4 h-4" />}
            Comparer
          </Button>
        </div>
        <p className="text-xs text-muted">
          Compare chaque fenêtre sur un holdout COMMUN — comparaison légitime entre tailles. Sans « Publier »,
          rien n'est écrit au registre.
        </p>
        {job && <JobResult job={job} />}
      </CardContent>
    </Card>
  );
}

// ── Page principale ───────────────────────────────────────────────────────────

export default function ModelsPage() {
  const { data, isLoading, isError, refetch, isFetching } = useMLRegistry();
  const entries = data?.models || [];
  const warnCount = entries.filter((e) => e.freshness_warning).length;
  const badgeVariant = entries.length === 0 ? 'default' : warnCount === 0 ? 'success' : 'warning';

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Modèles</h1>
          <p className="text-sm text-muted mt-1">
            Registre de modèles ML daté — versions, gate de promotion, entraînement et window sweep
          </p>
        </div>
        <Badge variant={badgeVariant}>
          <Database className="w-3 h-3" />
          {entries.length} recette{entries.length !== 1 ? 's' : ''}
          {warnCount > 0 ? ` · ${warnCount} alerte${warnCount !== 1 ? 's' : ''}` : ''}
        </Badge>
      </div>

      {/* Registry */}
      <Card>
        <CardHeader>
          <CardTitle>Registre de modèles</CardTitle>
          <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={cn('w-3.5 h-3.5', isFetching && 'animate-spin')} />
            Rafraîchir
          </Button>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
            </div>
          ) : isError ? (
            <div className="text-center py-8 text-red-400 text-sm">
              <AlertCircle className="w-8 h-8 mx-auto mb-2" />
              Erreur lors du chargement du registre
            </div>
          ) : (
            <RegistryTable entries={entries} />
          )}
          <div className="px-5 py-4 text-xs text-muted border-t border-border">
            « Version active » = ce que <code className="font-mono text-foreground">resolve()</code> retournerait
            maintenant (pin persistant prioritaire, sinon dernière version promue). Une recette sans version
            active a vu tous ses candidats rejetés par le gate — entraînez-en un nouveau ou promouvez
            manuellement un candidat existant depuis l'historique.
          </div>
        </CardContent>
      </Card>

      <TrainForm />
      <SweepForm />
    </div>
  );
}
