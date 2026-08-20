/**
 * OPT-002 — Top-5 trials table + best params block.
 *
 * Extrait du template Jinja2 `optimizer.html:673-698`.
 *
 * F1 (refactor) : la table est désormais rendue par le composant générique
 * `<DataTable>` (frontend/src/components/ui/data-table.tsx). On déclare
 * uniquement les colonnes et leur render — plus de logique de tri/pagination
 * dupliquée. La carte « Best params » reste inchangée (pas une table).
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import type { OptimizeTrial } from '@/types';
import { formatPctPoints, formatDrawdownPct } from '@/lib/backend-normalizers';

interface Props {
  trials: OptimizeTrial[];
  bestParams: Record<string, number | string> | null;
}

function fmt(v: number | null | undefined, decimals = 3): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(decimals);
}

export function TopTrialsTable({ trials, bestParams }: Props) {
  if ((!trials || trials.length === 0) && (!bestParams || Object.keys(bestParams).length === 0)) {
    return null;
  }

  // Colonnes pour <DataTable> — triable sur les métriques numériques.
  // On prend les 5 meilleurs trials (le backend les trie déjà par final score).
  const top5 = (trials ?? []).slice(0, 5);

  const columns: DataTableColumn<OptimizeTrial & { _rank: number }>[] = [
    {
      key: 'rank',
      header: '#',
      align: 'left',
      noSort: true,
      render: (t) => (
        <span className="font-mono">
          {t._rank === 0 ? '🏆' : ''} {t._rank + 1}
        </span>
      ),
    },
    {
      key: 'is_score',
      header: 'IS Score',
      align: 'right',
      render: (t) => <span className="font-mono">{fmt(t.is_score)}</span>,
    },
    {
      key: 'oos_score',
      header: 'OOS Score',
      align: 'right',
      render: (t) => <span className="font-mono">{fmt(t.oos_score)}</span>,
    },
    {
      key: 'final',
      header: 'Final',
      align: 'right',
      sortValue: (t) => t.final ?? t.final_score ?? 0,
      render: (t) => {
        const final = t.final ?? t.final_score;
        return <span className="font-mono font-bold">{fmt(final)}</span>;
      },
    },
    {
      key: 'oos_pnl',
      header: 'OOS PnL',
      align: 'right',
      render: (t) => (
        <span className={`font-mono ${t.oos_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
          {t.oos_pnl >= 0 ? '+' : ''}${t.oos_pnl.toFixed(2)}
        </span>
      ),
    },
    {
      key: 'oos_wr',
      header: 'OOS WR',
      align: 'right',
      render: (t) => <span className="font-mono">{formatPctPoints(t.oos_wr)}</span>,
    },
    {
      key: 'oos_dd',
      header: 'OOS DD',
      align: 'right',
      render: (t) => (
        <span className="font-mono text-rose-400">{formatDrawdownPct(t.oos_dd)}</span>
      ),
    },
    {
      key: 'overfit',
      header: 'Overfit',
      align: 'right',
      render: (t) => (
        <span className={`font-mono ${t.overfit > 2 ? 'text-amber-400' : 'text-emerald-400'}`}>
          {fmt(t.overfit)}
          {t.overfit > 2 ? ' ⚠' : ' ✓'}
        </span>
      ),
    },
  ];

  // On enrichit chaque trial avec son rang pour le rendu de la colonne #.
  const rows = top5.map((t, i) => ({ ...t, _rank: i }));

  return (
    <div className="space-y-3">
      {bestParams && Object.keys(bestParams).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">⭐ Best params</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="max-h-[200px] overflow-y-auto font-mono text-xs space-y-0.5">
              {Object.entries(bestParams).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-cyan-400">{k}</span>
                  <span className="text-muted-foreground">:</span>
                  <span className="text-emerald-400 ml-2">
                    {typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(6)) : String(v)}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {top5.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">🏆 Top-5 trials</CardTitle>
          </CardHeader>
          <CardContent>
            <DataTable
              columns={columns}
              rows={rows}
              sortable
              rowKey={(t) => `trial-${t._rank}`}
              emptyLabel="Aucun trial"
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
