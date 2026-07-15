'use client';

import { useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatDateTime } from '@/lib/utils';
import { useAuditResults } from '@/hooks/use-api';
import {
  Loader2, AlertCircle, Search, X, History, ClipboardList, Filter,
} from 'lucide-react';
import type { AuditResult } from '@/types';

// ── OOS score color helper ──────────────────────────────────────────────────

function oosColor(score: number): string {
  if (score >= 1.0) return 'text-emerald-400';
  if (score >= 0.5) return 'text-cyan-400';
  if (score >= 0) return 'text-amber-400';
  return 'text-red-400';
}

function oosVariant(score: number): 'success' | 'info' | 'warning' | 'danger' {
  if (score >= 1.0) return 'success';
  if (score >= 0.5) return 'info';
  if (score >= 0) return 'warning';
  return 'danger';
}

// ── Params drawer ───────────────────────────────────────────────────────────

function ParamsDrawer({
  row,
  onClose,
}: {
  row: AuditResult['results'][number] | null;
  onClose: () => void;
}) {
  if (!row) return null;
  const paramEntries = Object.entries(row.params || {});

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* Panel */}
      <aside className="relative w-full max-w-md bg-card border-l border-border h-full overflow-y-auto animate-slide-down">
        <div className="sticky top-0 bg-card border-b border-border p-4 flex items-center justify-between">
          <div>
            <div className="text-xs text-dim uppercase tracking-wider">Détails OOS</div>
            <div className="font-mono font-semibold mt-1">{row.slot_key}</div>
          </div>
          <Button size="icon" variant="ghost" onClick={onClose} aria-label="Fermer">
            <X className="w-4 h-4" />
          </Button>
        </div>

        <div className="p-4 space-y-4">
          {/* Meta */}
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Meta label="Stratégie" value={row.strategy} />
            <Meta label="TF" value={row.tf} />
            <Meta label="Symbole" value={row.symbol || '—'} />
            <Meta label="Run date" value={formatDateTime(row.run_date)} />
          </div>

          {/* Score */}
          <div className="p-3 rounded-lg bg-card-hover border border-border">
            <div className="text-xs text-dim mb-1">OOS Score</div>
            <div className={cn('font-mono text-2xl font-bold', oosColor(row.oos_score))}>
              {row.oos_score.toFixed(4)}
            </div>
          </div>

          {/* Params */}
          <div>
            <div className="text-xs text-dim uppercase tracking-wider mb-2">
              Paramètres ({paramEntries.length})
            </div>
            {paramEntries.length === 0 ? (
              <div className="text-sm text-muted">Aucun paramètre</div>
            ) : (
              <div className="space-y-1.5">
                {paramEntries.map(([k, v]) => (
                  <div
                    key={k}
                    className="flex items-center justify-between p-2 rounded-md bg-card-hover border border-border text-sm"
                  >
                    <span className="font-mono text-muted">{k}</span>
                    <span className="font-mono font-semibold text-foreground">
                      {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-dim uppercase tracking-wider">{label}</div>
      <div className="font-mono text-sm font-semibold mt-0.5 truncate">{value}</div>
    </div>
  );
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function AuditPage() {
  const { data, isLoading, isError } = useAuditResults();
  const [filter, setFilter] = useState('');
  const [selected, setSelected] = useState<AuditResult['results'][number] | null>(null);

  const payload = data as AuditResult | undefined;

  // Sort results by oos_score desc
  const sortedResults = useMemo(() => {
    const list = payload?.results || [];
    return [...list].sort((a, b) => (b.oos_score ?? 0) - (a.oos_score ?? 0));
  }, [payload]);

  // Filter by strategy (case-insensitive substring)
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return sortedResults;
    return sortedResults.filter(
      (r) =>
        r.strategy?.toLowerCase().includes(q) ||
        r.symbol?.toLowerCase().includes(q) ||
        r.tf?.toLowerCase().includes(q) ||
        r.slot_key?.toLowerCase().includes(q),
    );
  }, [sortedResults, filter]);

  // Historical backtests (object or array)
  const backtests = payload?.backtests;
  const backtestEntries: Array<[string, any]> = useMemo(() => {
    if (!backtests) return [];
    if (Array.isArray(backtests)) {
      return backtests.map((b, i) => [`#${i}`, b] as [string, any]);
    }
    return Object.entries(backtests);
  }, [backtests]);

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Audit OOS</h1>
          <p className="text-sm text-muted mt-1">
            Résultats OOS (Out-of-Sample) des optimisations appliquées
          </p>
        </div>
        <Badge variant="info">
          <ClipboardList className="w-3 h-3" />
          {payload?.total ?? 0} entrées
        </Badge>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[200px]">
            <label className="text-xs text-dim block mb-1 flex items-center gap-1">
              <Filter className="w-3 h-3" /> Filtre (stratégie / symbole / TF / slot)
            </label>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-dim" />
              <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="ex: trend_rider"
                className="w-full pl-9 pr-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
          </div>
          <div className="text-xs text-dim font-mono">
            {filtered.length} / {sortedResults.length} affichés
          </div>
        </CardContent>
      </Card>

      {/* Results table */}
      <Card>
        <CardHeader>
          <CardTitle>Résultats OOS (triés par score desc)</CardTitle>
          <Badge variant="purple">{filtered.length}</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
            </div>
          ) : isError ? (
            <div className="text-center py-8 text-red-400 text-sm">
              <AlertCircle className="w-8 h-8 mx-auto mb-2" />
              Erreur lors du chargement
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-12 text-muted text-sm">
              Aucun résultat OOS
            </div>
          ) : (
            <div className="overflow-x-auto max-h-[60vh]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card">
                  <tr className="text-left text-xs text-dim border-b border-border">
                    <th className="p-3 font-medium">Stratégie</th>
                    <th className="p-3 font-medium">TF</th>
                    <th className="p-3 font-medium">Symbole</th>
                    <th className="p-3 font-medium">Slot</th>
                    <th className="p-3 font-medium">Run date</th>
                    <th className="p-3 font-medium text-right">OOS Score</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r, i) => (
                    <tr
                      key={`${r.slot_key}-${i}`}
                      onClick={() => setSelected(r)}
                      className="border-b border-border/30 hover:bg-card-hover cursor-pointer transition-colors"
                    >
                      <td className="p-3 font-mono font-semibold">{r.strategy}</td>
                      <td className="p-3"><Badge variant="purple">{r.tf}</Badge></td>
                      <td className="p-3 text-muted">{r.symbol || '—'}</td>
                      <td className="p-3 text-xs text-dim font-mono">{r.slot_key}</td>
                      <td className="p-3 text-xs text-muted font-mono">{formatDateTime(r.run_date)}</td>
                      <td className={cn('p-3 text-right font-mono font-bold', oosColor(r.oos_score))}>
                        <Badge variant={oosVariant(r.oos_score)}>{r.oos_score.toFixed(4)}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Historical backtests */}
      <Card>
        <CardHeader>
          <CardTitle>Backtests historiques</CardTitle>
          <History className="w-4 h-4 text-primary-400" />
        </CardHeader>
        <CardContent className="p-0">
          {backtestEntries.length === 0 ? (
            <div className="text-center py-8 text-muted text-sm">
              Aucun backtest historique
            </div>
          ) : (
            <div className="overflow-x-auto max-h-96">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card">
                  <tr className="text-left text-xs text-dim border-b border-border">
                    <th className="p-3 font-medium">Clé</th>
                    <th className="p-3 font-medium">Slot</th>
                    <th className="p-3 font-medium text-right">Score</th>
                    <th className="p-3 font-medium">Date</th>
                    <th className="p-3 font-medium">Stratégie</th>
                  </tr>
                </thead>
                <tbody>
                  {backtestEntries.map(([k, b]) => (
                    <tr
                      key={k}
                      onClick={() => {
                        if (b?.params) {
                          setSelected({
                            strategy: b.strategy || b.slot_key?.split('::')?.[0] || '—',
                            tf: b.tf || b.slot_key?.split('::')?.[1] || '—',
                            symbol: b.symbol || '',
                            slot_key: b.slot_key || k,
                            run_date: b.run_date || b.timestamp || '',
                            oos_score: b.oos_score ?? b.score ?? 0,
                            params: b.params || {},
                          });
                        }
                      }}
                      className="border-b border-border/30 hover:bg-card-hover cursor-pointer"
                    >
                      <td className="p-3 text-xs text-dim font-mono">{k}</td>
                      <td className="p-3 text-xs font-mono">{b.slot_key || '—'}</td>
                      <td className={cn('p-3 text-right font-mono font-semibold', oosColor(b.oos_score ?? b.score ?? 0))}>
                        {(b.oos_score ?? b.score ?? 0).toFixed(4)}
                      </td>
                      <td className="p-3 text-xs text-muted font-mono">
                        {formatDateTime(b.run_date || b.timestamp)}
                      </td>
                      <td className="p-3 font-mono text-xs">{b.strategy || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Drawer */}
      <ParamsDrawer row={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
