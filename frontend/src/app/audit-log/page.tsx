'use client';

import { useAuditLog, useAuditLogStats } from '@/hooks/use-api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatDateTime } from '@/lib/utils';
import { ScrollText, Download, Activity, AlertTriangle, Shield, Power, Settings } from 'lucide-react';
import { useState } from 'react';

const ACTION_ICONS: Record<string, any> = {
  'bot.start': Power,
  'bot.stop': Power,
  'circuit_breaker.reset': AlertTriangle,
  'optimizer.apply': Activity,
  'config.change': Settings,
  'risk.reset': Shield,
};

const ACTION_COLORS: Record<string, string> = {
  'bot.start': 'text-emerald-400',
  'bot.stop': 'text-red-400',
  'circuit_breaker.reset': 'text-amber-400',
  'optimizer.apply': 'text-cyan-400',
  'config.change': 'text-purple-400',
  'risk.reset': 'text-amber-400',
};

export default function AuditLogPage() {
  const [actionFilter, setActionFilter] = useState('');
  const [limit, setLimit] = useState(100);
  const { data, isLoading } = useAuditLog({ limit, action: actionFilter });
  const { data: stats } = useAuditLogStats();

  const events = data?.events || [];
  const total = data?.total || 0;

  const exportCsv = () => {
    if (!events.length) return;
    const headers = ['id', 'ts', 'action', 'actor', 'ip', 'method', 'path', 'status_code', 'details'];
    const rows = events.map((e: any) => [
      e.id, e.ts, e.action, e.actor, e.ip, e.method, e.path, e.status_code,
      JSON.stringify(e.details),
    ]);
    const csv = [headers, ...rows].map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit_log_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <ScrollText className="w-6 h-6 text-primary-400" />
            Journal d&apos;Audit
          </h1>
          <p className="text-sm text-muted mt-1">
            Traçabilité des actions sensibles — {total} événement{total > 1 ? 's' : ''} au total
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={exportCsv} disabled={!events.length}>
          <Download className="w-4 h-4" />
          Export CSV
        </Button>
      </div>

      {/* Stats by action */}
      {stats && stats.by_action && Object.keys(stats.by_action).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Statistiques par action</CardTitle>
            <span className="text-xs text-dim">Total : {stats.total}</span>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
              {Object.entries(stats.by_action).map(([action, count]) => {
                const Icon = ACTION_ICONS[action] || Activity;
                const color = ACTION_COLORS[action] || 'text-muted';
                return (
                  <button
                    key={action}
                    onClick={() => setActionFilter(actionFilter === action ? '' : action)}
                    className={cn(
                      'p-3 rounded-lg border bg-card-hover text-left transition-all hover:scale-[1.02]',
                      actionFilter === action ? 'border-primary-400 ring-2 ring-primary-400/20' : 'border-border',
                    )}
                  >
                    <Icon className={cn('w-4 h-4 mb-2', color)} />
                    <div className="text-xs font-mono text-muted truncate">{action}</div>
                    <div className="text-lg font-bold mt-1">{count as number}</div>
                  </button>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Filters */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div>
            <label className="text-xs text-dim block mb-1">Filtre action</label>
            <input
              aria-label="Filtre action"
              type="text"
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              placeholder="ex: bot. , config, optimizer"
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-sm w-48"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1">Limite</label>
            <select
              aria-label="Limite"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-sm"
            >
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
            </select>
          </div>
          {actionFilter && (
            <Button variant="ghost" size="sm" onClick={() => setActionFilter('')}>
              ✕ Clear filter
            </Button>
          )}
        </CardContent>
      </Card>

      {/* Events table */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-dim border-b border-border">
                  <th className="p-3 font-medium">Timestamp</th>
                  <th className="p-3 font-medium">Action</th>
                  <th className="p-3 font-medium">Acteur</th>
                  <th className="p-3 font-medium">IP</th>
                  <th className="p-3 font-medium">Méthode</th>
                  <th className="p-3 font-medium">Path</th>
                  <th className="p-3 font-medium">Statut</th>
                  <th className="p-3 font-medium">Détails</th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr><td colSpan={8} className="p-8 text-center text-muted">Chargement...</td></tr>
                ) : events.length === 0 ? (
                  <tr><td colSpan={8} className="p-8 text-center text-muted">
                    Aucun événement d&apos;audit. Les actions sensibles (start/stop bot, apply params, etc.) apparaîtront ici.
                  </td></tr>
                ) : (
                  events.map((evt: any) => {
                    const Icon = ACTION_ICONS[evt.action] || Activity;
                    const color = ACTION_COLORS[evt.action] || 'text-muted';
                    return (
                      <tr key={evt.id} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-3 text-xs text-muted font-mono whitespace-nowrap">
                          {formatDateTime(evt.ts)}
                        </td>
                        <td className="p-3">
                          <span className={cn('inline-flex items-center gap-1.5 text-xs font-semibold', color)}>
                            <Icon className="w-3 h-3" />
                            {evt.action}
                          </span>
                        </td>
                        <td className="p-3 text-xs">{evt.actor}</td>
                        <td className="p-3 text-xs font-mono text-dim">{evt.ip || '—'}</td>
                        <td className="p-3">
                          <Badge variant={evt.method === 'POST' ? 'info' : evt.method === 'DELETE' ? 'danger' : 'default'}>
                            {evt.method}
                          </Badge>
                        </td>
                        <td className="p-3 text-xs font-mono text-dim truncate max-w-xs">{evt.path}</td>
                        <td className="p-3">
                          {evt.status_code > 0 && (
                            <span className={cn(
                              'text-xs font-mono',
                              evt.status_code < 300 ? 'text-emerald-400' :
                              evt.status_code < 500 ? 'text-amber-400' : 'text-red-400',
                            )}>
                              {evt.status_code}
                            </span>
                          )}
                        </td>
                        <td className="p-3 text-xs text-muted">
                          {evt.details && Object.keys(evt.details).length > 0 ? (
                            <details>
                              <summary className="cursor-pointer text-primary-400">Voir</summary>
                              <pre className="mt-1 p-2 rounded bg-background text-[10px] overflow-x-auto max-w-md">
                                {JSON.stringify(evt.details, null, 2)}
                              </pre>
                            </details>
                          ) : '—'}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
