'use client';

/**
 * Table « Trades recommandés » (Smart Graph / Smart Replay).
 *
 * Clique sur une ligne → callback parent pour afficher Entry / SL / TP sur le chart.
 * Affichée même si vide. Tri par défaut : signal_time décroissant.
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

/** Trade réalisé (backtest / replay). */
export interface RealizedTrade {
  side?: string;
  entry?: number;
  exit?: number;
  entry_price?: number;
  exit_price?: number;
  stop?: number;
  tp?: number;
  take_profit?: number;
  pnl?: number;
  pnl_pct?: number;
  /** Gain potentiel au moment du signal (entry → TP). */
  gain_pct?: number;
  rr?: number;
  score?: number;
  score_min?: number;
  distance_pct?: number | null;
  setup?: string;
  exit_reason?: string;
  reason?: string;
  entry_bar?: number;
  exit_bar?: number;
  /** Epoch secondes (bougie d'entrée / signal). */
  signal_time?: number | null;
  entry_time?: string | number | null;
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
  title = 'Trades recommandés',
}: {
  plans?: TradePlan[] | null;
  onSelectPlan?: (plan: TradePlan) => void;
  selectedPlan?: TradePlan | null;
  title?: string;
}) {
  // Défaut : signal le plus récent en premier
  const [sortKey, setSortKey] = useState<SortKey>('signal_time');
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

  const selectedKey = selectedPlan ? planKey(selectedPlan) : null;

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setAsc((v) => !v);
    else {
      setSortKey(k);
      // Signal / score / RR / gain : décroissant par défaut ; distance croissante
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
          {title} ({sorted.length})
        </CardTitle>
        {onSelectPlan && (
          <p className="text-[11px] text-muted font-normal">
            Cliquez une ligne pour afficher Entry / SL / TP sur le graphique
          </p>
        )}
      </CardHeader>
      <CardContent className="p-0">
        {sorted.length === 0 ? (
          <p className="text-xs text-muted p-4 text-center">
            Aucun trade recommandé pour cette paire / TF
          </p>
        ) : (
        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-xs">
            <caption className="sr-only">
              Trades recommandés, triés par {sortKey}
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
        )}
      </CardContent>
    </Card>
  );
}

function realizedSignalTime(t: RealizedTrade): number | null {
  if (t.signal_time != null && Number.isFinite(Number(t.signal_time))) {
    return Number(t.signal_time);
  }
  const et = t.entry_time;
  if (et == null) return null;
  if (typeof et === 'number' && Number.isFinite(et)) return et;
  const d = new Date(String(et));
  return Number.isNaN(d.getTime()) ? null : d.getTime() / 1000;
}

function expectedGainPct(t: RealizedTrade): number | null {
  if (t.gain_pct != null && Number.isFinite(Number(t.gain_pct))) return Number(t.gain_pct);
  const entry = Number(t.entry ?? t.entry_price);
  const tp = Number(t.tp ?? t.take_profit);
  if (!Number.isFinite(entry) || entry <= 0 || !Number.isFinite(tp) || tp <= 0) return null;
  return (Math.abs(tp - entry) / entry) * 100;
}

function expectedRr(t: RealizedTrade): number | null {
  if (t.rr != null && Number.isFinite(Number(t.rr))) return Number(t.rr);
  const entry = Number(t.entry ?? t.entry_price);
  const stop = Number(t.stop);
  const tp = Number(t.tp ?? t.take_profit);
  if (![entry, stop, tp].every((x) => Number.isFinite(x) && x > 0)) return null;
  const risk = Math.abs(entry - stop);
  if (risk <= 0) return null;
  return Math.abs(tp - entry) / risk;
}

/** Table des trades réalisés (backtest / Smart Replay).
 * Même structure que Trades recommandés :
 * Signal | Résultat | Sens | Setup | Entrée | SL | TP | Gain espéré | PnL% | RR | Dist | Score | Raison
 */
export function RealizedTradesTable({
  trades,
  title,
  strategy = 'smart_money',
}: {
  trades?: RealizedTrade[] | null;
  /** Si omis : « Trades réalisés (N — Backtest {strategy}) ». */
  title?: string;
  /** Stratégie utilisée pour le backtest (affichée dans le titre). */
  strategy?: string;
}) {
  type RealizedSortKey = 'signal_time' | 'gain_pct' | 'pnl_pct' | 'rr' | 'score_min';
  const [sortKey, setSortKey] = useState<RealizedSortKey>('signal_time');
  const [asc, setAsc] = useState(false);

  const list = useMemo(() => {
    const arr = Array.isArray(trades) ? [...trades] : [];
    return arr
      .filter((t) => t.exit != null || t.exit_price != null || t.exit_bar != null)
      .sort((a, b) => {
        const dir = asc ? 1 : -1;
        const pick = (t: RealizedTrade): number => {
          if (sortKey === 'signal_time') return realizedSignalTime(t) ?? 0;
          if (sortKey === 'gain_pct') return expectedGainPct(t) ?? 0;
          if (sortKey === 'pnl_pct') return Number(t.pnl_pct ?? t.pnl ?? 0);
          if (sortKey === 'rr') return expectedRr(t) ?? 0;
          return Number(t.score_min ?? t.score ?? 0);
        };
        return (pick(a) - pick(b)) * dir;
      });
  }, [trades, sortKey, asc]);

  const closed = list.length;
  const wins = list.filter((t) => Number(t.pnl ?? 0) > 0).length;
  const displayTitle = title
    ?? `Trades réalisés (${closed} — Backtest ${strategy})`;

  const toggleSort = (k: RealizedSortKey) => {
    if (k === sortKey) setAsc((v) => !v);
    else {
      setSortKey(k);
      setAsc(false);
    }
  };

  const SortableTh = ({ k, children, title: tip, align = 'right' }: {
    k: RealizedSortKey; children: React.ReactNode; title?: string; align?: 'left' | 'right';
  }) => (
    <th
      scope="col"
      className={cn('p-2 font-medium', align === 'right' ? 'text-right' : 'text-left')}
      aria-sort={sortKey === k ? (asc ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        onClick={() => toggleSort(k)}
        title={tip}
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
        <CardTitle className="text-sm flex items-center gap-2">
          {displayTitle}
        </CardTitle>
        {closed > 0 && (
          <span className="text-[10px] text-dim">
            {wins} gagnants · {closed - wins} perdants · WR{' '}
            {closed ? ((wins / closed) * 100).toFixed(0) : 0}%
          </span>
        )}
      </CardHeader>
      <CardContent className="p-0">
        {closed === 0 ? (
          <p className="text-xs text-muted p-4 text-center">Aucun trade réalisé</p>
        ) : (
          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-xs">
              <caption className="sr-only">
                Trades réalisés ({strategy}), triés par {sortKey}
              </caption>
              <thead className="sticky top-0 bg-card">
                <tr className="text-left text-dim border-b border-border">
                  <SortableTh k="signal_time" align="left" title="Date/heure du signal d'entrée">
                    Signal
                  </SortableTh>
                  <th scope="col" className="p-2 font-medium">Résultat</th>
                  <th scope="col" className="p-2 font-medium">Sens</th>
                  <th scope="col" className="p-2 font-medium">Setup</th>
                  <th scope="col" className="p-2 font-medium text-right">Entrée</th>
                  <th scope="col" className="p-2 font-medium text-right">SL</th>
                  <th scope="col" className="p-2 font-medium text-right">TP</th>
                  <SortableTh k="gain_pct" title="Gain potentiel entry → TP au moment du signal">
                    Gain espéré
                  </SortableTh>
                  <SortableTh k="pnl_pct" title="PnL réalisé en %">
                    PnL%
                  </SortableTh>
                  <SortableTh k="rr">RR</SortableTh>
                  <th scope="col" className="p-2 font-medium text-right" title="Non applicable une fois clôturé">
                    Dist
                  </th>
                  <SortableTh k="score_min" title="Score du signal">
                    Score
                  </SortableTh>
                  <th scope="col" className="p-2 font-medium">Raison</th>
                </tr>
              </thead>
              <tbody>
                {list.map((t, i) => {
                  const pnl = Number(t.pnl ?? 0);
                  const pct = t.pnl_pct != null ? Number(t.pnl_pct) : null;
                  const win = pnl > 0;
                  const loss = pnl < 0;
                  const gain = expectedGainPct(t);
                  const rr = expectedRr(t);
                  const score = t.score_min ?? t.score;
                  const isLong = t.side === 'long';
                  const reason = t.exit_reason || t.reason || '';
                  return (
                    <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                      <td className="p-2 font-mono whitespace-nowrap text-muted">
                        {formatSignalTime(realizedSignalTime(t)) ?? '—'}
                      </td>
                      <td className="p-2">
                        <Badge variant={win ? 'success' : loss ? 'danger' : 'muted'}>
                          {win ? 'Gagnant' : loss ? 'Perdant' : '—'}
                        </Badge>
                      </td>
                      <td className="p-2">
                        <span className={cn(
                          'font-semibold',
                          isLong ? 'text-emerald-400' : 'text-red-400',
                        )}>
                          {String(t.side || '—').toUpperCase()}
                        </span>
                      </td>
                      <td className="p-2 text-cyan-400 font-mono">{t.setup || '—'}</td>
                      <td className="p-2 text-right font-mono">{fmtPrice(t.entry ?? t.entry_price)}</td>
                      <td className="p-2 text-right font-mono text-red-400">{fmtPrice(t.stop)}</td>
                      <td className="p-2 text-right font-mono text-emerald-400">
                        {fmtPrice(t.tp ?? t.take_profit)}
                      </td>
                      <td className="p-2 text-right font-mono">
                        {gain != null ? `${gain.toFixed(2)}%` : '—'}
                      </td>
                      <td className={cn(
                        'p-2 text-right font-mono font-semibold',
                        win ? 'text-emerald-400' : loss ? 'text-red-400' : 'text-muted',
                      )}>
                        {pct != null && Number.isFinite(pct)
                          ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`
                          : pnl !== 0
                            ? formatUSD(pnl, { sign: true })
                            : '—'}
                      </td>
                      <td className={cn(
                        'p-2 text-right font-mono',
                        (rr ?? 0) >= 2 ? 'text-emerald-400' : 'text-muted',
                      )}>
                        {rr != null ? rr.toFixed(2) : '—'}
                      </td>
                      <td className="p-2 text-right font-mono text-muted">
                        {t.distance_pct != null && Number.isFinite(Number(t.distance_pct))
                          ? `${Number(t.distance_pct).toFixed(2)}%`
                          : '—'}
                      </td>
                      <td className="p-2 text-right font-mono">
                        {score != null && Number.isFinite(Number(score))
                          ? Number(score).toFixed(2)
                          : '—'}
                      </td>
                      <td className="p-2 text-muted truncate max-w-[16rem]" title={reason}>
                        {reason || '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
