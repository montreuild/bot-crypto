'use client';

/**
 * Table « Plans recommandés » (Smart Graph).
 *
 * Clique sur une ligne → callback parent pour afficher Entry / SL / TP sur le chart.
 */

import { useMemo, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ArrowUpDown, Target } from 'lucide-react';
import { cn, formatUSD } from '@/lib/utils';

export interface TradePlan {
  status?: string;
  side?: string;
  setup?: string;
  score_min?: number;
  entry?: number;
  stop?: number;
  tp?: number;
  gain_pct?: number;
  rr?: number;
  tp_source?: string;
  distance_pct?: number;
  trigger?: string;
  reason?: string;
  zone_low?: number;
  zone_high?: number;
  /** Epoch **secondes**. */
  signal_time?: number | null;
}

type SortKey = 'score_min' | 'rr' | 'gain_pct' | 'distance_pct' | 'signal_time';

function formatSignalTime(v: number | null | undefined): string | null {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return null;
  const n = Number(v);
  const d = new Date(n < 1e11 ? n * 1000 : n);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function fmtPrice(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '—';
  return formatUSD(n);
}

const STATUS_LABEL: Record<string, { label: string; variant: 'success' | 'warning' | 'muted' }> = {
  immediate: { label: 'Immédiat', variant: 'success' },
  pending: { label: 'En attente', variant: 'warning' },
};

function planKey(p: TradePlan): string {
  return `${p.side}|${p.setup}|${p.entry}|${p.stop}|${p.tp}|${p.signal_time}`;
}

export function TradePlansTable({
  plans,
  onSelectPlan,
  selectedPlan,
}: {
  plans: TradePlan[];
  onSelectPlan?: (plan: TradePlan) => void;
  selectedPlan?: TradePlan | null;
}) {
  const [sortKey, setSortKey] = useState<SortKey>('score_min');
  const [asc, setAsc] = useState(false);

  const sorted = useMemo(() => {
    const list = Array.isArray(plans) ? [...plans] : [];
    return list.sort((a, b) => {
      const dir = asc ? 1 : -1;
      const av = Number(a[sortKey] ?? 0);
      const bv = Number(b[sortKey] ?? 0);
      return (av - bv) * dir;
    });
  }, [plans, sortKey, asc]);

  if (sorted.length === 0) return null;

  const selectedKey = selectedPlan ? planKey(selectedPlan) : null;

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setAsc((v) => !v);
    else {
      setSortKey(k);
      setAsc(k === 'distance_pct');
    }
  };

  const SortableTh = ({ k, children, title, align = 'right' }: {
    k: SortKey; children: React.ReactNode; title?: string; align?: 'left' | 'right';
  }) => (
    <th
      scope="col"
      className={cn('p-2 font-medium', align === 'right' ? 'text-right' : 'text-left')}
      aria-sort={sortKey === k ? (asc ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        onClick={() => toggleSort(k)}
        title={title}
        className="inline-flex items-center gap-1 hover:text-foreground focus:outline-none focus:ring-1 focus:ring-primary-400 rounded"
      >
        {children}
        <ArrowUpDown className={cn('w-3 h-3', sortKey === k ? 'text-primary-400' : 'opacity-40')} />
      </button>
    </th>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Target className="w-3.5 h-3.5" />
          Plans recommandés ({sorted.length})
        </CardTitle>
        {onSelectPlan && (
          <p className="text-[11px] text-muted font-normal">
            Cliquez une ligne pour afficher Entry / SL / TP sur le graphique
          </p>
        )}
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-xs">
            <caption className="sr-only">
              Plans de trade recommandés, triés par {sortKey}
            </caption>
            <thead className="sticky top-0 bg-card">
              <tr className="text-left text-dim border-b border-border">
                <SortableTh
                  k="signal_time"
                  align="left"
                  title="Bougie de référence du plan"
                >
                  Signal
                </SortableTh>
                <th scope="col" className="p-2 font-medium">Statut</th>
                <th scope="col" className="p-2 font-medium">Sens</th>
                <th scope="col" className="p-2 font-medium">Setup</th>
                <th scope="col" className="p-2 font-medium text-right">Entrée</th>
                <th scope="col" className="p-2 font-medium text-right">SL</th>
                <th scope="col" className="p-2 font-medium text-right">TP</th>
                <SortableTh k="gain_pct">Gain</SortableTh>
                <SortableTh k="rr">RR</SortableTh>
                <SortableTh k="distance_pct" title="Distance du prix actuel à la zone d'entrée">
                  Dist
                </SortableTh>
                <SortableTh k="score_min" title="Score minimum">
                  Score
                </SortableTh>
                <th scope="col" className="p-2 font-medium">Déclencheur</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p, i) => {
                const st = STATUS_LABEL[String(p.status ?? '')] ?? { label: p.status || '—', variant: 'muted' as const };
                const isLong = p.side === 'long';
                const key = planKey(p);
                const isSelected = selectedKey === key;
                return (
                  <tr
                    key={i}
                    onClick={() => onSelectPlan?.(p)}
                    className={cn(
                      'border-b border-border/30 transition-colors',
                      onSelectPlan && 'cursor-pointer hover:bg-card-hover',
                      isSelected && 'bg-primary-500/10 ring-1 ring-inset ring-primary-400/40',
                    )}
                    aria-selected={isSelected}
                  >
                    <td className="p-2 font-mono whitespace-nowrap text-muted">
                      {formatSignalTime(p.signal_time) ?? '—'}
                    </td>
                    <td className="p-2">
                      <Badge variant={st.variant}>{st.label}</Badge>
                    </td>
                    <td className="p-2">
                      <span className={cn('font-semibold', isLong ? 'text-emerald-400' : 'text-red-400')}>
                        {p.side?.toUpperCase() || '—'}
                      </span>
                    </td>
                    <td className="p-2 text-cyan-400 font-mono">{p.setup || '—'}</td>
                    <td className="p-2 text-right font-mono">{fmtPrice(p.entry)}</td>
                    <td className="p-2 text-right font-mono text-red-400">{fmtPrice(p.stop)}</td>
                    <td className="p-2 text-right font-mono text-emerald-400">{fmtPrice(p.tp)}</td>
                    <td className="p-2 text-right font-mono">
                      {Number.isFinite(Number(p.gain_pct)) ? `${Number(p.gain_pct).toFixed(2)}%` : '—'}
                    </td>
                    <td className={cn('p-2 text-right font-mono', Number(p.rr ?? 0) >= 2 ? 'text-emerald-400' : 'text-muted')}>
                      {Number.isFinite(Number(p.rr)) ? Number(p.rr).toFixed(2) : '—'}
                    </td>
                    <td className="p-2 text-right font-mono text-muted">
                      {Number.isFinite(Number(p.distance_pct)) ? `${Number(p.distance_pct).toFixed(2)}%` : '—'}
                    </td>
                    <td className="p-2 text-right font-mono">
                      {Number.isFinite(Number(p.score_min)) ? Number(p.score_min).toFixed(2) : '—'}
                    </td>
                    <td className="p-2 text-muted truncate max-w-[16rem]" title={p.trigger || p.reason || ''}>
                      {p.trigger || p.reason || '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
