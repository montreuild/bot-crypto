'use client';

import { Badge } from '@/components/ui/badge';
import { cn, formatDateTime } from '@/lib/utils';
import type {
  ModelFeatureImportance, ModelRegimeAuc, ModelTrainMeta,
  ModelRegimeFeatureImportance, ModelRegimeSimilarity,
} from '@/types';

// ── Badges ──────────────────────────────────────────────────────────────────

export function AucBadge({ auc, trainEnd }: { auc: number | null | undefined; trainEnd?: string | null }) {
  if (auc == null || (auc === 0 && !trainEnd)) {
    return <Badge title="AUC non mesurée (artefact sans provenance datée)">n/m</Badge>;
  }
  const variant = auc >= 0.65 ? 'success' : auc >= 0.55 ? 'warning' : 'danger';
  return <Badge variant={variant}>{auc.toFixed(3)}</Badge>;
}

export function DecisionBadge({ decision }: { decision: string | null | undefined }) {
  if (!decision) return <Badge>—</Badge>;
  const ok = decision === 'promote' || decision === 'initial' || decision === 'manual';
  return <Badge variant={ok ? 'success' : 'danger'}>{decision}</Badge>;
}

// ── Diagnostics tables ──────────────────────────────────────────────────────

export function FeatureImportanceTable({ items }: { items?: ModelFeatureImportance[] }) {
  if (!items || items.length === 0) return null;
  return (
    <table className="w-full text-xs">
      <tbody>
        {items.slice(0, 10).map((f, i) => (
          <tr key={f.feature} className="border-b border-border/20">
            <td className="p-1 text-dim font-mono">{i + 1}.</td>
            <td className="p-1 font-mono">{f.feature}</td>
            <td className="p-1 text-right font-mono text-cyan-400">{f.gain.toFixed(4)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RegimeAucTable({ byRegime }: { byRegime?: Record<string, ModelRegimeAuc> }) {
  const entries = Object.entries(byRegime || {});
  if (entries.length === 0) return null;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-dim border-b border-border">
          <th className="p-1 text-left">Régime</th>
          <th className="p-1 text-right">N</th>
          <th className="p-1 text-right">AUC</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} className="border-b border-border/20">
            <td className="p-1 font-mono">{k}</td>
            <td className="p-1 text-right font-mono text-muted">{v.n ?? '—'}</td>
            <td className="p-1 text-right"><AucBadge auc={v.auc} trainEnd="x" /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RegimeFeatureColumns({ byRegime }: { byRegime?: Record<string, ModelRegimeFeatureImportance> }) {
  const entries = Object.entries(byRegime || {}).filter(([, v]) => (v.top || []).length > 0);
  if (entries.length === 0) return null;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {entries.map(([regime, v]) => (
        <div key={regime}>
          <div className="text-xs text-muted mb-1">{regime}</div>
          <table className="w-full text-xs">
            <tbody>
              {(v.top || []).slice(0, 10).map((f, i) => (
                <tr key={f.feature} className="border-b border-border/20">
                  <td className="p-1 text-dim font-mono">{i + 1}.</td>
                  <td className="p-1 font-mono">{f.feature}</td>
                  <td className="p-1 text-right font-mono text-cyan-400">{f.contrib.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

export function RegimeSimilarityTable({ sim }: { sim?: Record<string, ModelRegimeSimilarity> }) {
  const entries = Object.entries(sim || {});
  if (entries.length === 0) return null;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-dim border-b border-border">
          <th className="p-1 text-left">Paire</th>
          <th className="p-1 text-right">Spearman</th>
          <th className="p-1 text-right">Overlap</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} className="border-b border-border/20">
            <td className="p-1 font-mono">{k}</td>
            <td className="p-1 text-right font-mono">{v.spearman != null ? Number(v.spearman).toFixed(3) : '—'}</td>
            <td className="p-1 text-right font-mono text-muted">{v.top_overlap != null ? `${v.top_overlap}` : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function DiagnosticsPanel({ trainMeta, title }: { trainMeta?: ModelTrainMeta; title?: string }) {
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
        {title ?? 'Diagnostics (version active)'}
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
            (pas de spécialisation exploitable). Un écart net serait la piste d&apos;un routing
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
