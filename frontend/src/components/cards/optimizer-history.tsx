'use client';

/**
 * P0-5 — Card « Historique des optimisations » (changelog).
 *
 * Consomme `api.getConfigChangelog(100)` qui expose `optimizer_changelog.json`
 * (route `/api/config/changelog`). Affiche l'historique des apply d'optimisation
 * (params avant/après, oos_score, timestamp) dans une table `<DataTable>`.
 *
 * Permet à l'utilisateur de répondre à « quand ont été appliqués ces params,
 * quels étaient les anciens ? » sans lire le JSON à la main.
 *
 * Replié par défaut (collapsible) pour ne pas surcharger la page.
 */

import { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { ChevronDown, ChevronRight, History, Loader2, Trash2 } from 'lucide-react';
import {formatDateTime, errorMessage} from '@/lib/utils';

interface ChangelogEntry {
  timestamp?: string;
  strategy?: string;
  timeframe?: string;
  symbol?: string;
  oos_score?: number;
  params_before?: Record<string, unknown>;
  params_after?: Record<string, unknown>;
}

export function OptimizerHistory({ className }: { className?: string }) {
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();
  const [purging, setPurging] = useState(false);

  const handlePurge = async () => {
    setPurging(true);
    try {
      const res = await api.optimizePurge(24, 200);
      toast.success(`${res.purged} job(s) purgé(s), ${res.remaining} restant(s)`);
      qc.invalidateQueries({ queryKey: ['optimizeStatus'] });
    } catch (e) {
      toast.error(`Erreur purge : ${errorMessage(e)}`);
    } finally {
      setPurging(false);
    }
  };

  const { data, isLoading } = useQuery({
    queryKey: ['configChangelog', 100],
    queryFn: () => api.getConfigChangelog(100),
    enabled: expanded, // ne fetch que si déplié
    staleTime: 60 * 1000,
  });

  // Le backend retourne soit {entries: [...]} soit [...] directement
  const entries: ChangelogEntry[] = Array.isArray(data)
    ? data
    : (data?.entries ?? data?.changelog ?? []);

  const columns: DataTableColumn<ChangelogEntry>[] = [
    {
      key: 'timestamp',
      header: 'Date',
      align: 'left',
      sortValue: (e) => e.timestamp ?? '',
      render: (e) => (
        <span className="font-mono text-xs text-muted">
          {e.timestamp ? formatDateTime(e.timestamp) : '—'}
        </span>
      ),
    },
    {
      key: 'strategy',
      header: 'Stratégie',
      align: 'left',
      sortValue: (e) => e.strategy ?? '',
      render: (e) => (
        <span className="font-mono text-cyan-400 text-xs">{e.strategy ?? '—'}</span>
      ),
    },
    {
      key: 'timeframe',
      header: 'TF',
      align: 'left',
      noSort: true,
      render: (e) => (
        <Badge variant="purple" className="text-[10px]">{e.timeframe ?? '—'}</Badge>
      ),
    },
    {
      key: 'symbol',
      header: 'Symbole',
      align: 'left',
      sortValue: (e) => e.symbol ?? '',
      render: (e) => <span className="font-mono text-xs">{e.symbol ?? '—'}</span>,
    },
    {
      key: 'oos_score',
      header: 'OOS Score',
      align: 'right',
      sortValue: (e) => e.oos_score ?? 0,
      render: (e) => (
        <span className="font-mono text-xs text-emerald-400">
          {e.oos_score != null ? Number(e.oos_score).toFixed(4) : '—'}
        </span>
      ),
    },
    {
      key: 'params',
      header: 'Params (avant → après)',
      noSort: true,
      render: (e) => {
        const before = e.params_before ?? {};
        const after = e.params_after ?? {};
        const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
        if (keys.length === 0) return <span className="text-xs text-dim">—</span>;
        return (
          <div className="text-[10px] font-mono space-y-0.5 max-w-md">
            {keys.slice(0, 3).map((k) => {
              const b = before[k];
              const a = after[k];
              const changed = JSON.stringify(b) !== JSON.stringify(a);
              return (
                <div key={k} className="flex items-center gap-1">
                  <span className="text-muted">{k}:</span>
                  <span className={changed ? 'text-amber-400 line-through' : 'text-dim'}>
                    {String(b ?? '—')}
                  </span>
                  {changed && (
                    <>
                      <span className="text-dim">→</span>
                      <span className="text-emerald-400">{String(a ?? '—')}</span>
                    </>
                  )}
                </div>
              );
            })}
            {keys.length > 3 && (
              <div className="text-dim">+{keys.length - 3} autre(s)</div>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle
          className="flex items-center justify-between cursor-pointer"
          onClick={() => setExpanded(!expanded)}
        >
          <span className="flex items-center gap-2">
            {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            <History className="w-4 h-4" />
            Historique des optimisations
          </span>
          {entries.length > 0 && (
            <div className="flex items-center gap-2">
              <Badge variant="muted" className="text-[10px]">
                {entries.length} entrée{entries.length > 1 ? 's' : ''}
              </Badge>
              <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); handlePurge(); }} disabled={purging}
                className="h-6 text-[10px] text-muted hover:text-danger" title="Purger les jobs > 24h (garde 200)">
                {purging ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />} Purger
              </Button>
            </div>
          )}
        </CardTitle>
        {expanded && (
          <p className="text-[11px] text-muted-foreground">
            Trace des apply d&apos;optimisation (params avant/après, score OOS,
            timestamp). Source : <code className="text-cyan-400">optimizer_changelog.json</code>{' '}
            (rotation 200 entrées).
          </p>
        )}
      </CardHeader>
      {expanded && (
        <CardContent>
          {isLoading ? (
            <div className="p-6 text-center text-muted text-sm">
              <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
              Chargement...
            </div>
          ) : entries.length === 0 ? (
            <div className="p-6 text-center text-muted text-sm">
              Aucun apply enregistré pour l&apos;instant. Lancez une optimisation
              avec auto_apply ou cliquez « Apply » sur un job terminé.
            </div>
          ) : (
            <DataTable
              columns={columns}
              rows={entries}
              sortable
              initialSortKey="timestamp"
              initialSortAsc={false}
              rowKey={(e, i) => `${e.timestamp ?? i}-${e.strategy ?? ''}-${i}`}
              emptyLabel="Aucune entrée"
            />
          )}
        </CardContent>
      )}
    </Card>
  );
}
