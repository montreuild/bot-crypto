'use client';

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {cn, formatDateTime, errorMessage} from '@/lib/utils';
import { toast } from 'sonner';
import {
  useMLRegistryVersions, useMLRegistryDecisions,
  usePinModel, useUnpinModel, usePromoteModel,
} from '@/hooks/use-api';
import {
  Loader2, AlertCircle, Pin, PinOff, ChevronDown, ChevronRight,
  ThumbsUp, ThumbsDown, Microscope,
} from 'lucide-react';
import { OverfittingGateBadge } from '@/components/cards/overfitting-gate-badge';
import {
  AucBadge, DecisionBadge, DiagnosticsPanel,
} from '@/components/models/ml-badges-and-diagnostics';
import type {
  ModelRegistryEntry, ModelArtifact, ModelDecision, ModelTrainMeta,
} from '@/types';

export function VersionRow({ entry, version }: { entry: ModelRegistryEntry; version: ModelArtifact }) {
  const pin = usePinModel();
  const unpin = useUnpinModel();
  const promote = usePromoteModel();
  const isPinned = entry.pinned_version_id === version.version_id;
  const busy = pin.isPending || unpin.isPending || promote.isPending;
  const [showDiag, setShowDiag] = useState(false);
  const hasDiag = !!version.train_meta && Object.keys(version.train_meta).length > 0;

  const handlePinToggle = async () => {
    try {
      if (isPinned) {
        await unpin.mutateAsync({ tf: entry.tf, recipe: entry.recipe });
        toast.success('Pin retiré');
      } else {
        await pin.mutateAsync({ tf: entry.tf, recipe: entry.recipe, versionId: version.version_id });
        toast.success('Version épinglée');
      }
    } catch (e) {
      toast.error(`Erreur : ${errorMessage(e)}`);
    }
  };

  const [confirmState, setConfirmState] = useState<{ open: boolean; decision: 'manual' | 'keep' }>({ open: false, decision: 'manual' });
  const openConfirm = (decision: 'manual' | 'keep') => setConfirmState({ open: true, decision });

  const handlePromote = async () => {
    const { decision } = confirmState;
    try {
      await promote.mutateAsync({ tf: entry.tf, recipe: entry.recipe, versionId: version.version_id, decision });
      toast.success('Décision mise à jour');
    } catch (e) {
      toast.error(`Erreur : ${errorMessage(e)}`);
    } finally {
      setConfirmState({ open: false, decision });
    }
  };

  return (
    <>
      <tr className="border-b border-border/20">
        <td className="p-2 font-mono">{version.version_id}</td>
        <td className="p-2 text-muted font-mono">{formatDateTime(version.train_end)}</td>
        <td className="p-2 text-right font-mono">{version.n_bars ?? '—'}</td>
        <td className="p-2 space-x-1.5">
          <AucBadge auc={version.auc} trainEnd={version.train_end} />
          <OverfittingGateBadge overfittingGate={(version as any).overfitting_gate} size="sm" showAuc={false} />
        </td>
        <td className="p-2"><DecisionBadge decision={version.gate_decision} /></td>
        <td className="p-2">
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" variant="outline" onClick={handlePinToggle} disabled={busy}
              className={isPinned ? 'text-purple-400 border-purple-500/40 bg-purple-500/10' : ''}>
              {isPinned ? <PinOff className="w-3 h-3" /> : <Pin className="w-3 h-3" />}
              {isPinned ? 'Retirer' : 'Épingler'}
            </Button>
            <Button size="sm" variant="success" onClick={() => openConfirm('manual')} disabled={busy}>
              <ThumbsUp className="w-3 h-3" /> Promouvoir
            </Button>
            <Button size="sm" variant="danger" onClick={() => openConfirm('keep')} disabled={busy}>
              <ThumbsDown className="w-3 h-3" /> Rejeter
            </Button>
            {hasDiag && (
              <Button size="sm" variant="ghost" onClick={() => setShowDiag((v) => !v)}>
                <Microscope className="w-3 h-3" /> Diagnostics
              </Button>
            )}
          </div>
        </td>
      </tr>
      {hasDiag && showDiag && (
        <tr className="border-b border-border/20">
          <td colSpan={6} className="p-2">
            <DiagnosticsPanel trainMeta={version.train_meta} title={`Diagnostics — ${version.version_id}`} />
          </td>
        </tr>
      )}
      <ConfirmDialog
        open={confirmState.open}
        onOpenChange={(open) => setConfirmState((s) => ({ ...s, open }))}
        title={`${confirmState.decision === 'manual' ? 'Promouvoir' : 'Rejeter'} la version ${version.version_id} ?`}
        description={`Cette action marquera la version ${version.version_id} pour ${entry.tf}/${entry.recipe} comme décision ${confirmState.decision === 'manual' ? 'manuelle (promue)' : 'conservée (rejetée)'}. L'action est journalisée dans l'audit log.`}
        confirmLabel={confirmState.decision === 'manual' ? 'Promouvoir' : 'Rejeter'}
        variant={confirmState.decision === 'manual' ? 'success' : 'danger'}
        isLoading={promote.isPending}
        onConfirm={handlePromote}
      />
    </>
  );
}

export function DecisionsTable({ decisions }: { decisions: ModelDecision[] }) {
  if (decisions.length === 0) return <div className="text-xs text-dim">Aucune décision journalisée.</div>;
  return (
    <table className="w-full text-xs">
      <thead><tr className="text-left text-dim border-b border-border/50">
        <th className="p-2 font-medium">Date</th><th className="p-2 font-medium">Version</th>
        <th className="p-2 font-medium">Décision</th><th className="p-2 font-medium">Source</th>
        <th className="p-2 font-medium">Raison</th>
      </tr></thead>
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

// P2-4 : sparkline AUC + P2-9 : colonne Provenance
export function RegistryRow({ entry }: { entry: ModelRegistryEntry }) {
  const [expanded, setExpanded] = useState(false);
  const { data: versionsData, isLoading: versionsLoading } = useMLRegistryVersions(
    expanded ? entry.tf : null, expanded ? entry.recipe : null);
  const { data: decisionsData } = useMLRegistryDecisions(
    expanded ? entry.tf : null, expanded ? entry.recipe : null);
  const active = entry.active;

  return (
    <>
      <tr className="border-b border-border/30 hover:bg-card-hover">
        <td className="p-3"><Badge variant="purple">{entry.tf}</Badge></td>
        <td className="p-3 font-mono text-xs">{entry.recipe}</td>
        <td className="p-3 font-mono text-xs text-muted">{entry.train_symbol ?? '—'}</td>
        <td className="p-3 font-mono text-xs">{active ? active.version_id : <span className="text-dim">aucune</span>}</td>
        <td className="p-3 text-xs text-muted font-mono">{active ? formatDateTime(active.train_end) : '—'}</td>
        <td className="p-3">{active ? <AucBadge auc={active.auc} trainEnd={active.train_end} /> : '—'}</td>
        <td className="p-3">{active ? <DecisionBadge decision={active.gate_decision} /> : '—'}</td>
        <td className="p-3">
          <div className="flex flex-col gap-1 items-start">
            {entry.freshness_warning ? (
              <Badge variant="warning" title={entry.freshness_warning} className="whitespace-nowrap">
                <AlertCircle className="w-3 h-3" />
                {entry.freshness_warning.length > 28 ? `${entry.freshness_warning.slice(0, 28)}…` : entry.freshness_warning}
              </Badge>
            ) : <Badge variant="success">OK</Badge>}
            {entry.pinned_version_id && (
              <Badge variant="purple" title={`Épinglé sur ${entry.pinned_version_id}`}>
                <Pin className="w-3 h-3" /> pin
              </Badge>
            )}
          </div>
        </td>
        <td className="p-3 text-right font-mono">{entry.n_versions}</td>
        {/* P2-9 : colonne Provenance */}
        <td className="p-3">
          {active && (active.recipe_hash || active.git_commit) ? (
            <div className="flex flex-col gap-0.5 text-[10px] font-mono text-dim">
              {active.recipe_hash && <span title={`Hash features: ${active.recipe_hash}`}>{active.recipe_hash.slice(0, 8)}</span>}
              {active.git_commit && <span title={`Git commit: ${active.git_commit}`}>{active.git_commit.slice(0, 8)}</span>}
            </div>
          ) : <span className="text-dim text-xs">—</span>}
        </td>
        <td className="p-3">
          <Button size="sm" variant="ghost" onClick={() => setExpanded((v) => !v)}>
            {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            {expanded ? 'Masquer' : 'Détails'}
          </Button>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-black/10">
          <td colSpan={11} className="p-4">
            <div className="text-[10px] uppercase tracking-wider text-dim font-semibold mb-2">Versions</div>
            {/* P2-4 : sparkline AUC */}
            {(() => {
              const versions = versionsData?.versions || [];
              const aucs = versions.map((v) => v.auc).filter((a) => a != null && a > 0) as number[];
              if (aucs.length >= 2) {
                const min = Math.min(...aucs);
                const max = Math.max(...aucs);
                const range = max - min || 1;
                return (
                  <div className="flex items-center gap-1 mb-3">
                    <span className="text-[10px] text-dim mr-1">AUC:</span>
                    {aucs.map((auc, i) => {
                      const heightPct = ((auc - min) / range) * 100;
                      const isLast = i === aucs.length - 1;
                      return (
                        <div key={i}
                          className={cn('w-2 rounded-sm transition-all',
                            isLast ? 'bg-emerald-400' : auc >= 0.55 ? 'bg-cyan-400/60' : 'bg-amber-400/60')}
                          style={{ height: `${Math.max(8, heightPct * 0.4 + 4)}px` }}
                          title={`v${i + 1}: AUC=${auc.toFixed(3)}`}
                        />
                      );
                    })}
                    <span className="text-[10px] text-dim ml-1 font-mono">{min.toFixed(3)} → {max.toFixed(3)}</span>
                  </div>
                );
              }
              return null;
            })()}
            {versionsLoading ? (
              <div className="flex items-center py-4"><Loader2 className="w-4 h-4 animate-spin text-primary-400" /></div>
            ) : (
              <div className="overflow-x-auto mb-4" tabIndex={0} role="group" aria-label="Tableau défilable">
                <table className="w-full text-xs">
                  <thead><tr className="text-left text-dim border-b border-border/50">
                    <th className="p-2 font-medium">Version</th>
                    <th className="p-2 font-medium">Entraînée jusqu&apos;au</th>
                    <th className="p-2 font-medium text-right">N barres</th>
                    <th className="p-2 font-medium">AUC</th>
                    <th className="p-2 font-medium">Décision</th>
                    <th className="p-2 font-medium">Actions</th>
                  </tr></thead>
                  <tbody>
                    {(versionsData?.versions || []).map((v) => (
                      <VersionRow key={v.version_id} entry={entry} version={v} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="text-[10px] uppercase tracking-wider text-dim font-semibold mb-2">Décisions récentes</div>
            <DecisionsTable decisions={decisionsData?.decisions || []} />
            <DiagnosticsPanel trainMeta={entry.active?.train_meta} />
          </td>
        </tr>
      )}
    </>
  );
}

export function RegistryTable({ entries }: { entries: ModelRegistryEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="text-sm text-muted text-center py-6 space-y-2">
        <p>Aucun modèle dans le registre.</p>
        <p className="text-xs">Lancez un premier entraînement ci-dessous ou depuis l&apos;onglet{' '}
          <a href="/lab?tab=ml" className="text-cyan-400 hover:underline">Lab · ML</a>.</p>
      </div>
    );
  }
  return (
    <div className="overflow-x-auto" tabIndex={0} role="group" aria-label="Tableau défilable">
      <table className="w-full text-sm">
        <thead><tr className="text-left text-xs text-dim border-b border-border">
          <th className="p-3 font-medium">TF</th>
          <th className="p-3 font-medium">Recette</th>
          <th className="p-3 font-medium" title="Symbole dont proviennent les bougies d'entraînement">Entraîné sur</th>
          <th className="p-3 font-medium">Version active</th>
          <th className="p-3 font-medium">Entraînée jusqu&apos;au</th>
          <th className="p-3 font-medium">AUC</th>
          <th className="p-3 font-medium">Décision</th>
          <th className="p-3 font-medium">Fraîcheur</th>
          <th className="p-3 font-medium text-right">Versions</th>
          <th className="p-3 font-medium" title="Hash features + commit Git">Provenance</th>
          <th className="p-3 font-medium">Actions</th>
        </tr></thead>
        <tbody>
          {entries.map((e) => (
            <RegistryRow key={`${e.tf}|${e.recipe}`} entry={e} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
