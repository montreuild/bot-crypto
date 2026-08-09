'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useMLRegistry } from '@/hooks/use-api';
import { Loader2, Database, AlertCircle, RefreshCw } from 'lucide-react';
import { RecentMlJobs } from '@/components/cards/recent-ml-jobs';
import { MLVersioningAudit } from '@/components/cards/ml-versioning-audit';
import { RegistryTable } from '@/components/models/ml-registry-components';
import { TrainForm, SweepForm } from '@/components/models/ml-forms';
import type { ModelRegistryEntry } from '@/types';

export default function ModelsPage() {
  const { data, isLoading, isError, refetch, isFetching } = useMLRegistry();
  const entries = data?.models || [];
  const warnCount = entries.filter((e: ModelRegistryEntry) => e.freshness_warning).length;
  const badgeVariant = entries.length === 0 ? 'default' : warnCount === 0 ? 'success' : 'warning';

  // P2-2 : filtres RegistryTable
  const [searchQuery, setSearchQuery] = useState('');
  const [tfFilter, setTfFilter] = useState('');
  const [pinFilter, setPinFilter] = useState(false);
  const [warnFilter, setWarnFilter] = useState(false);

  const filteredEntries = entries.filter((e: ModelRegistryEntry) => {
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      if (!e.recipe?.toLowerCase().includes(q) && !e.tf?.toLowerCase().includes(q) && !e.train_symbol?.toLowerCase().includes(q))
        return false;
    }
    if (tfFilter && e.tf !== tfFilter) return false;
    if (pinFilter && !e.pinned_version_id) return false;
    if (warnFilter && !e.freshness_warning) return false;
    return true;
  });

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

      {/* P2-5 : bannière de fraîcheur globale si modèles périmés */}
      {warnCount > 0 && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400 text-sm">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span>
            <strong>{warnCount} modèle(s) périmé(s)</strong> — vérifiez le scheduler de
            réentraînement. Le seuil par défaut est 2× l&apos;intervalle configuré.
          </span>
          <a href="/settings?tab=risk" className="text-amber-300 hover:underline ml-auto text-xs">
            Réglages →
          </a>
        </div>
      )}

      {/* P2-2 : filtres RegistryTable */}
      {entries.length > 0 && (
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="text-xs text-dim block mb-1">Recherche</label>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="recette, TF, symbole…"
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-sm w-48"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1">TF</label>
            <select
              value={tfFilter}
              onChange={(e) => setTfFilter(e.target.value)}
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-sm"
            >
              <option value="">Tous</option>
              <option value="15m">15m</option>
              <option value="30m">30m</option>
              <option value="1h">1h</option>
              <option value="4h">4h</option>
              <option value="1d">1d</option>
            </select>
          </div>
          <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer pb-2">
            <input type="checkbox" checked={pinFilter} onChange={(e) => setPinFilter(e.target.checked)} className="rounded" />
            Épinglées seulement
          </label>
          <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer pb-2">
            <input type="checkbox" checked={warnFilter} onChange={(e) => setWarnFilter(e.target.checked)} className="rounded" />
            Avec alerte fraîcheur
          </label>
        </div>
      )}

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
            <RegistryTable entries={filteredEntries} />
          )}
          <div className="px-5 py-4 text-xs text-muted border-t border-border">
            « Version active » = ce que <code className="font-mono text-foreground">resolve()</code> retournerait
            maintenant (pin persistant prioritaire, sinon dernière version promue). Une recette sans version
            active a vu tous ses candidats rejetés par le gate — entraînez-en un nouveau ou promouvez
            manuellement un candidat existant depuis l&apos;historique.
          </div>
        </CardContent>
      </Card>

      <TrainForm />
      <SweepForm />

      {/* P0-5 — Audit versioning des modèles (migration_check) */}
      <MLVersioningAudit />

      {/* P1-2 — Jobs ML récents (route /api/ml/jobs) */}
      <RecentMlJobs limit={20} />
    </div>
  );
}
