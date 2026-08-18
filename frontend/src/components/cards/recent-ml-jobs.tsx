'use client';

/**
 * P1-2 — Card « Entraînements ML récents ».
 *
 * Affiche la liste des jobs ML (train + sweep) récents via la route
 * `/api/ml/jobs`. Permet à l'utilisateur de retrouver un job après
 * fermeture du dialog (jobs orphelins) et de suivre les entraînements
 * en cours.
 *
 * Fonctionnalités :
 *   - Filtre par statut (tous / running / done / error) via chips
 *   - Tableau rendu via `<DataTable>` (F1)
 *   - Polling adaptatif : 5 s si jobs running, 60 s sinon
 *   - Bouton « Supprimer » pour les jobs terminés (avec confirm)
 *   - Lien vers `/models` pour voir le résultat dans le registre
 */

import { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { useMLJobs, useDeleteMLJob } from '@/hooks/use-api';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import {
  Loader2, Trash2, ExternalLink, History,
} from 'lucide-react';
import {cn, errorMessage} from '@/lib/utils';
import type { MLJobStatus } from '@/types';

const STATUS_VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'muted' | 'info'> = {
  running: 'info',
  done: 'success',
  error: 'danger',
  cancelled: 'muted',
  pending: 'warning',
  queued: 'warning',
  skipped: 'muted',
};

const STATUS_LABEL: Record<string, string> = {
  running: 'En cours',
  done: 'Terminé',
  error: 'Erreur',
  cancelled: 'Annulé',
  pending: 'En attente',
  queued: 'En file',
  skipped: 'Ignoré',
};

const KIND_LABEL: Record<string, string> = {
  train: 'Entraînement',
  sweep: 'Sweep',
};

function formatRelativeTime(timestamp: number | undefined): string {
  if (!timestamp) return '—';
  const now = Date.now() / 1000;
  const diff = now - timestamp;
  if (diff < 60) return `${Math.floor(diff)}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}min`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}j`;
}

interface Props {
  /** Limite de jobs à afficher (défaut 20). */
  limit?: number;
  /** Filtre statut initial (défaut : tous). */
  initialStatus?: string;
  className?: string;
}

export function RecentMlJobs({ limit = 20, initialStatus = '', className }: Props) {
  const [statusFilter, setStatusFilter] = useState(initialStatus);
  const { data, isLoading } = useMLJobs({
    status: statusFilter || undefined,
    limit,
  });
  const deleteJob = useDeleteMLJob();

  const jobs = data?.jobs ?? [];
  const runningCount = data?.running_count ?? 0;
  const total = data?.total ?? 0;

  const handleDelete = async (jobId: string) => {
    if (!confirm('Supprimer ce job de la liste ?')) return;
    try {
      await deleteJob.mutateAsync(jobId);
      toast.success('Job supprimé');
    } catch (e) {
      toast.error(`Erreur : ${errorMessage(e) || 'inconnue'}`);
    }
  };

  // Colonnes pour <DataTable>
  const columns: DataTableColumn<MLJobStatus>[] = [
    {
      key: 'kind',
      header: 'Type',
      align: 'left',
      noSort: true,
      render: (j) => (
        <Badge variant="muted" className="text-[10px]">
          {KIND_LABEL[j.kind] ?? j.kind ?? '—'}
        </Badge>
      ),
    },
    {
      key: 'strategy',
      header: 'Stratégie',
      align: 'left',
      sortValue: (j) => j.strategy ?? '',
      render: (j) => (
        <span className="font-mono text-cyan-400 text-xs">{j.strategy ?? '—'}</span>
      ),
    },
    {
      key: 'symbol',
      header: 'Symbole',
      align: 'left',
      sortValue: (j) => j.symbol ?? '',
      render: (j) => <span className="font-mono text-xs">{j.symbol ?? '—'}</span>,
    },
    {
      key: 'tf',
      header: 'TF',
      align: 'left',
      noSort: true,
      render: (j) => <span className="font-mono text-xs text-dim">{j.tf ?? '—'}</span>,
    },
    {
      key: 'status',
      header: 'Statut',
      align: 'left',
      sortValue: (j) => j.status ?? '',
      render: (j) => {
        const status = j.status ?? '—';
        return (
          <Badge variant={STATUS_VARIANT[status] ?? 'muted'} className="text-[10px]">
            {j.status === 'running' && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
            {STATUS_LABEL[status] ?? status}
          </Badge>
        );
      },
    },
    {
      key: 'started_at',
      header: 'Début',
      align: 'right',
      sortValue: (j) => j.started_at ?? 0,
      render: (j) => (
        <span className="font-mono text-xs text-muted">
          {formatRelativeTime(j.started_at)}
        </span>
      ),
    },
    {
      key: 'result',
      header: 'Résultat',
      align: 'right',
      noSort: true,
      render: (j) => {
        if (j.status === 'running') return <span className="text-xs text-dim">—</span>;
        if (j.status === 'error') {
          return (
            <span className="text-xs text-red-400 truncate max-w-[200px] block" title={j.error ?? ''}>
              {j.error ?? 'Erreur'}
            </span>
          );
        }
        if (j.status === 'done' && j.result) {
          const decision = j.result.decision ?? j.result.verdict ?? '—';
          const auc = j.result.auc ?? j.result.candidate?.auc_amp;
          return (
            <div className="flex items-center gap-2 justify-end">
              {auc != null && (
                <span className="font-mono text-xs text-emerald-400">
                  AUC {Number(auc).toFixed(3)}
                </span>
              )}
              <Badge variant="muted" className="text-[10px]">{decision}</Badge>
            </div>
          );
        }
        return <span className="text-xs text-dim">—</span>;
      },
    },
    {
      key: 'actions',
      header: 'Actions',
      align: 'right',
      noSort: true,
      render: (j) => (
        <div className="flex items-center gap-1 justify-end">
          {j.status === 'done' && (
            <a
              href={`/models?recipe=${encodeURIComponent(j.strategy ?? '')}`}
              className="text-cyan-400 hover:text-cyan-300 p-1"
              title="Voir dans le registre"
            >
              <ExternalLink className="w-3 h-3" />
            </a>
          )}
          {j.status !== 'running' && (
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                handleDelete(j.job_id ?? '');
              }}
              disabled={deleteJob.isPending}
              className="h-6 w-6 p-0 text-muted hover:text-red-400"
              title="Supprimer"
            >
              <Trash2 className="w-3 h-3" />
            </Button>
          )}
        </div>
      ),
    },
  ];

  // Filtres chips
  const FILTERS = [
    { value: '', label: 'Tous' },
    { value: 'running', label: 'En cours' },
    { value: 'done', label: 'Terminés' },
    { value: 'error', label: 'Erreurs' },
  ];

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span className="flex items-center gap-2">
            <History className="w-4 h-4" />
            Entraînements récents
          </span>
          <div className="flex items-center gap-2 text-[10px] text-muted">
            {runningCount > 0 && (
              <Badge variant="info" className="text-[10px]">
                <Loader2 className="w-2.5 h-2.5 animate-spin" />
                {runningCount} en cours
              </Badge>
            )}
            <span>{total} job(s) au total</span>
          </div>
        </CardTitle>
        {/* Filtres chips */}
        <div className="flex items-center gap-1.5 flex-wrap mt-1">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatusFilter(f.value)}
              className={cn(
                'text-[10px] px-2 py-0.5 rounded-md border transition-colors',
                statusFilter === f.value
                  ? 'bg-primary-500/10 border-primary-500/30 text-primary-400'
                  : 'bg-card-hover/30 border-border text-muted hover:text-foreground',
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="p-6 text-center text-muted text-sm">
            <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
            Chargement...
          </div>
        ) : jobs.length === 0 ? (
          <div className="p-6 text-center text-muted text-sm">
            Aucun job ML récent. Lancez un entraînement depuis l&apos;onglet{' '}
            <a href="/lab?tab=ml" className="text-cyan-400 hover:underline">Lab · ML</a>{' '}
            ou depuis{' '}
            <a href="/models" className="text-cyan-400 hover:underline">Modèles</a>.
          </div>
        ) : (
          <DataTable
            columns={columns}
            rows={jobs}
            sortable
            initialSortKey="started_at"
            initialSortAsc={false}
            rowKey={(j) => j.job_id ?? ''}
            emptyLabel="Aucun job"
          />
        )}
      </CardContent>
    </Card>
  );
}
