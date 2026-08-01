'use client';

/**
 * E3-F4-US5 — Table « Plans recommandés » (Smart Graph).
 *
 * La table existait déjà en JSX dans `/smartgraph`, mais avec 7 des 10 colonnes
 * de l'ancienne `smartgraph.html` : **Statut**, **Gain**, **Dist** et **Score**
 * manquaient, et le tri par score n'existait pas. Or ce sont précisément ces
 * colonnes qui rendent la table actionnable — sans « Statut » on ne distingue
 * pas un signal immédiat d'un plan en attente, et sans « Dist » on ne sait pas
 * si le prix est proche de la zone d'entrée.
 *
 * Contrat réel de `Strategy.trade_plans()` (app/strategies/smart_money.py),
 * exposé tel quel par `/api/scanner/smc` sous la clé `trade_plans` :
 *   { status, side, setup, score_min, entry, stop, tp, gain_pct, rr,
 *     tp_source, distance_pct, trigger, reason, zone_low, zone_high }
 *
 * ⚠ `score_min` est un score MINIMUM : les confluences qui dépendent de la
 * bougie de déclenchement (volume, couleur) ne sont pas connues d'avance. La
 * colonne est libellée en conséquence.
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
  /** Epoch **secondes** — cf. `signal_time` dans `Strategy.trade_plans()`. */
  signal_time?: number | null;
}

type SortKey = 'score_min' | 'rr' | 'gain_pct' | 'distance_pct' | 'signal_time';

/**
 * Horodatage du signal, en date + heure:minutes.
 *
 * Le backend sérialise les temps SMC en epoch **secondes** (comme
 * `time_start`/`time_end` des order blocks), pas en millisecondes : passer la
 * valeur brute à `new Date()` donnerait janvier 1970.
 */
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

const STATUS_LABEL: Record<string, { label: string; variant: 'success' | 'warning' | 'muted' }> = {
  immediate: { label: 'Immédiat', variant: 'success' },
  pending: { label: 'En attente', variant: 'warning' },
};

export function TradePlansTable({ plans }: { plans: TradePlan[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('score_min');
  const [asc, setAsc] = useState(false);

  const sorted = useMemo(() => {
    const list = Array.isArray(plans) ? [...plans] : [];
    return list.sort((a, b) => {
      // `distance_pct` se lit à l'envers : plus c'est proche, mieux c'est.
      const dir = asc ? 1 : -1;
      const av = Number(a[sortKey] ?? 0);
      const bv = Number(b[sortKey] ?? 0);
      return (av - bv) * dir;
    });
  }, [plans, sortKey, asc]);

  if (sorted.length === 0) return null;

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setAsc((v) => !v);
    else {
      setSortKey(k);
      // Par défaut : score/RR/gain décroissants, distance croissante (le plus proche d'abord).
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
                  title="Bougie de référence : pour un plan immédiat, la bougie courante ; pour un plan en attente, celle où la zone s'est formée"
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
                <SortableTh k="score_min" title="Score minimum : les confluences de la bougie de déclenchement ne sont pas connues d'avance">
                  Score
                </SortableTh>
                <th scope="col" className="p-2 font-medium">Déclencheur</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p, i) => {
                const st = STATUS_LABEL[String(p.status ?? '')] ?? { label: p.status || '—', variant: 'muted' as const };
                const isLong = p.side === 'long';
                return (
                  <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
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
                    <td className="p-2 text-right font-mono">{formatUSD(Number(p.entry ?? 0))}</td>
                    <td className="p-2 text-right font-mono text-red-400">{formatUSD(Number(p.stop ?? 0))}</td>
                    <td className="p-2 text-right font-mono text-emerald-400">{formatUSD(Number(p.tp ?? 0))}</td>
                    <td className="p-2 text-right font-mono">{Number(p.gain_pct ?? 0).toFixed(2)}%</td>
                    <td className={cn('p-2 text-right font-mono', Number(p.rr ?? 0) >= 2 ? 'text-emerald-400' : 'text-muted')}>
                      {Number(p.rr ?? 0).toFixed(2)}
                    </td>
                    <td className="p-2 text-right font-mono text-muted">
                      {Number(p.distance_pct ?? 0).toFixed(2)}%
                    </td>
                    <td className="p-2 text-right font-mono">{Number(p.score_min ?? 0).toFixed(2)}</td>
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
