'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { useMLDecisionsRecent } from '@/hooks/use-api';
import { Loader2, GitBranch } from 'lucide-react';
import { formatDateTime } from '@/lib/utils';

interface MLDecision {
  ts?: string; version_id?: string; decision?: string; source?: string;
  reason?: string; _recipe?: string; _tf?: string;
}

const DV: Record<string, 'success' | 'danger' | 'warning' | 'muted'> = {
  promote: 'success', initial: 'success', manual: 'success', keep: 'danger', failed: 'danger', skipped: 'muted',
};

export function MLDecisionsRecent({ className }: { className?: string }) {
  const { data, isLoading } = useMLDecisionsRecent(50);
  const decisions = data?.decisions ?? [];
  const columns: DataTableColumn<MLDecision>[] = [
    { key: 'ts', header: 'Date', align: 'left', sortValue: (d) => d.ts ?? '', render: (d) => <span className="font-mono text-xs text-muted">{d.ts ? formatDateTime(d.ts) : '—'}</span> },
    { key: '_recipe', header: 'Recette', align: 'left', sortValue: (d) => d._recipe ?? '', render: (d) => <span className="font-mono text-cyan-500 text-xs">{d._recipe ?? '—'}</span> },
    { key: '_tf', header: 'TF', noSort: true, render: (d) => <Badge variant="purple" className="text-[10px]">{d._tf ?? '—'}</Badge> },
    { key: 'version_id', header: 'Version', align: 'left', sortValue: (d) => d.version_id ?? '', render: (d) => <span className="font-mono text-xs">{d.version_id?.slice(0, 12) ?? '—'}</span> },
    { key: 'decision', header: 'Décision', align: 'left', sortValue: (d) => d.decision ?? '', render: (d) => <Badge variant={DV[d.decision ?? ''] ?? 'muted'} className="text-[10px]">{d.decision ?? '—'}</Badge> },
    { key: 'source', header: 'Source', noSort: true, render: (d) => <span className="text-xs text-dim">{d.source ?? '—'}</span> },
    { key: 'reason', header: 'Raison', noSort: true, render: (d) => <span className="text-xs text-muted truncate max-w-[200px] block" title={d.reason ?? ''}>{d.reason ?? '—'}</span> },
  ];
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <GitBranch className="w-4 h-4" />
          Décisions ML récentes
          {decisions.length > 0 && <Badge variant="muted" className="text-[10px]">{decisions.length}</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? <div className="p-6 text-center text-muted text-sm"><Loader2 className="w-4 h-4 animate-spin inline mr-2" />Chargement...</div>
        : decisions.length === 0 ? <div className="p-6 text-center text-muted text-sm">Aucune décision journalisée.</div>
        : <DataTable columns={columns} rows={decisions} sortable initialSortKey="ts" initialSortAsc={false} rowKey={(d, i) => `${d.ts ?? i}-${d.version_id ?? ''}`} emptyLabel="Aucune décision" />}
      </CardContent>
    </Card>
  );
}
