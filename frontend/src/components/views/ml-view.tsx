'use client';

/**
 * Onglet ML Train de `/lab` — état des modèles ML et cache des bougies.
 *
 * Lot Laboratoire : cette vue était la page `/ml`, que l'onglet se contentait
 * de teaser (« intégration native prévue au Sprint 9 »). `/ml` est désormais
 * en 308 vers `/lab?tab=ml`.
 *
 * ⚠ `/models` (registre versionné, gate de promotion) reste une page à part
 * entière : elle n'est pas dans le plan de fusion. L'onglet y renvoie.
 *
 * Sprint 4 (ML specs) — l'optimiseur ML est désormais intégré nativement
 * (`<OptimizerView filterMl />`). Le renvoi vers `/lab?tab=optimizer` est
 * supprimé : l'utilisateur peut lancer et appliquer une optimisation ML
 * directement depuis l'onglet ML.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn, timeAgo, formatPct } from '@/lib/utils';
import { useMLStrategyInfo, useCandlesStats } from '@/hooks/use-api';
import { MLRecipesList } from '@/components/cards/ml-recipes-list';
import { RecentMlJobs } from '@/components/cards/recent-ml-jobs';
import { MLVersioningAudit } from '@/components/cards/ml-versioning-audit';
import { OptimizerView } from '@/components/views/optimizer-view';
import type { CandleDatasetStats } from '@/types';
import {
  Loader2, BrainCircuit, Database, CheckCircle2, XCircle,
  AlertCircle, Cpu,
} from 'lucide-react';
import type { MLStrategyInfo } from '@/types';

// ── Strategy table ──────────────────────────────────────────────────────────

function StrategyTable({ strategies }: { strategies: Record<string, MLStrategyInfo> }) {
  const entries = Object.entries(strategies || {});
  if (entries.length === 0) {
    return <div className="text-sm text-muted text-center py-6">Aucune stratégie ML</div>;
  }
  // `tabIndex={0}` : une zone défilable doit être atteignable au clavier, sinon
  // son contenu est inaccessible sans souris (axe : scrollable-region-focusable).
  // `role="group"` + `aria-label` évitent qu'un lecteur d'écran annonce un
  // conteneur anonyme focusable.
  return (
    <div className="overflow-x-auto" tabIndex={0} role="group" aria-label="Tableau défilable">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-dim border-b border-border">
            <th className="p-3 font-medium">Stratégie</th>
            <th className="p-3 font-medium">Statut</th>
            <th className="p-3 font-medium text-right">Best AUC</th>
            <th className="p-3 font-medium text-right">Prochain retrain</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, info]) => {
            const trained = !!info?.is_trained;
            const auc = info?.best_auc ?? 0;
            const aucQuality = auc >= 0.7 ? 'text-emerald-400' : auc >= 0.55 ? 'text-amber-400' : 'text-red-400';
            return (
              <tr key={name} className="border-b border-border/30 hover:bg-card-hover">
                <td className="p-3">
                  <div className="flex items-center gap-2">
                    <BrainCircuit className="w-4 h-4 text-purple-400 flex-shrink-0" />
                    <span className="font-mono font-semibold">{name}</span>
                  </div>
                </td>
                <td className="p-3">
                  {trained ? (
                    <Badge variant="success">
                      <CheckCircle2 className="w-3 h-3" />
                      Entraîné
                    </Badge>
                  ) : (
                    <Badge variant="danger">
                      <XCircle className="w-3 h-3" />
                      Non entraîné
                    </Badge>
                  )}
                </td>
                <td className={cn('p-3 text-right font-mono font-semibold', aucQuality)}>
                  {auc > 0 ? auc.toFixed(4) : '—'}
                </td>
                <td className="p-3 text-right text-xs text-muted font-mono">
                  {info?.next_retrain_at ? timeAgo(info.next_retrain_at) : 'jamais'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Candles cache stats ─────────────────────────────────────────────────────

/**
 * LAB-01 / LAB-09 — inventaire du cache OHLCV.
 *
 * `size_kb` est déjà en kilo-octets côté serveur, d'où l'absence de conversion.
 * Les colonnes affichées sont celles que la route remplit : `from`/`to` sont
 * structurellement nuls sur l'inventaire complet (cf. `CandleDatasetStats`),
 * la complétude et les trous, eux, portent une information.
 */
function CandlesStatsTable({ store }: { store: CandleDatasetStats[] }) {
  if (!store?.length) {
    return <div className="text-sm text-muted text-center py-6">Cache bougies vide</div>;
  }
  const rows = [...store].sort(
    (a, b) => a.symbol.localeCompare(b.symbol) || a.tf.localeCompare(b.tf));

  return (
    <div className="overflow-x-auto" tabIndex={0} role="group" aria-label="Tableau défilable">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-dim border-b border-border">
            <th className="p-3 font-medium">Symbole</th>
            <th className="p-3 font-medium">TF</th>
            <th className="p-3 font-medium text-right">Bougies</th>
            <th className="p-3 font-medium text-right">Complétude</th>
            <th className="p-3 font-medium text-right">Trous</th>
            <th className="p-3 font-medium text-right">Taille</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const pct = r.completeness == null ? null : r.completeness * 100;
            return (
              <tr key={`${r.symbol}-${r.tf}`} className="border-b border-border/30 hover:bg-card-hover">
                <td className="p-3 font-mono font-semibold">{r.symbol}</td>
                <td className="p-3"><Badge variant="purple">{r.tf}</Badge></td>
                <td className="p-3 text-right font-mono">{(r.bars ?? 0).toLocaleString('fr-FR')}</td>
                <td className={cn('p-3 text-right font-mono',
                  pct == null ? 'text-muted'
                    : pct >= 99 ? 'text-emerald-400'
                    : pct >= 95 ? 'text-amber-400' : 'text-red-400')}>
                  {pct == null ? 'non mesurée' : formatPct(pct, 1, false)}
                </td>
                <td className={cn('p-3 text-right font-mono',
                  (r.gaps ?? 0) > 0 ? 'text-amber-400' : 'text-muted')}>
                  {r.gaps ?? 0}
                </td>
                <td className="p-3 text-right font-mono text-muted">
                  {r.size_kb ? formatKb(r.size_kb) : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function formatKb(kb: number): string {
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  if (kb < 1024 * 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${(kb / 1024 / 1024).toFixed(1)} GB`;
}

// ── Vue ─────────────────────────────────────────────────────────────────────

export function MLView() {
  const { data: mlData, isLoading: mlLoading, isError: mlError } = useMLStrategyInfo();
  const { data: candlesData, isLoading: candlesLoading, isError: candlesError } = useCandlesStats();

  const strategies = (mlData?.strategies || {}) as Record<string, MLStrategyInfo>;
  const trainedCount = Object.values(strategies).filter((s) => s?.is_trained).length;
  const totalCount = Object.keys(strategies).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Modèles ML</h2>
          <p className="text-sm text-muted mt-1">
            Gestion des modèles machine learning et cache des bougies
          </p>
        </div>
        <Badge variant={trainedCount === totalCount && totalCount > 0 ? 'success' : 'warning'}>
          <BrainCircuit className="w-3 h-3" />
          {trainedCount}/{totalCount} entraînés
        </Badge>
      </div>

      {/* P2-1 — Layout 2 colonnes sur desktop (lg+), empilé sur mobile.
          Gauche : Stratégies ML vivantes + Cache bougies.
          Droite : Recettes ML + Audit versioning.
          Bas (pleine largeur) : Jobs récents + Optimiseur ML. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Colonne gauche : runtime */}
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Stratégies ML</CardTitle>
              <Cpu className="w-4 h-4 text-purple-400" />
            </CardHeader>
            <CardContent className="p-0">
              {mlLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
                </div>
              ) : mlError ? (
                <div className="text-center py-8 text-red-400 text-sm">
                  <AlertCircle className="w-8 h-8 mx-auto mb-2" />
                  Erreur lors du chargement des stratégies ML
                </div>
              ) : (
                <StrategyTable strategies={strategies} />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Cache bougies (datasets)</CardTitle>
              <Database className="w-4 h-4 text-primary-400" />
            </CardHeader>
            <CardContent className="p-0">
              {candlesLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
                </div>
              ) : candlesError ? (
                <div className="text-center py-8 text-red-400 text-sm">
                  <AlertCircle className="w-8 h-8 mx-auto mb-2" />
                  Erreur lors du chargement du cache
                </div>
              ) : (
                <CandlesStatsTable store={candlesData?.store ?? []} />
              )}
            </CardContent>
          </Card>
        </div>

        {/* Colonne droite : recettes + audit */}
        <div className="space-y-6">
          {/* S9-F3-US1 — Recettes ML disponibles */}
          <MLRecipesList />

          {/* P0-5 — Audit versioning des modèles (migration_check) */}
          <MLVersioningAudit />
        </div>
      </div>

      {/* P1-2 — Jobs ML récents (pleine largeur) */}
      <RecentMlJobs limit={20} />

      {/* ML-001 — Optimiseur ML intégré nativement. `filterMl` restreint
          les chips aux stratégies ML déclarées dans `/optimize/spaces`,
          active le label cyan « ⬡ Lancer l'optimisation ML », le warning
          omnibus (ML-006), le label complet du checkbox `ml_tune_hp`
          (ML-002), la note Apply « modèle sauvegardé automatiquement »
          (ML-007) et l'invalidation des queries ML après apply (ML-004). */}
      <OptimizerView filterMl />
    </div>
  );
}
