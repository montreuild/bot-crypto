'use client';

/**
 * Table « Trades recommandés » (Smart Graph / Smart Replay).
 *
 * Clique sur une ligne → callback parent pour afficher Entry / SL / TP sur le chart.
 * Affichée même si vide. Tri par défaut : signal_time décroissant.
 *
 * F1 (refactor final) : `TradePlansTable` ET `RealizedTradesTable` migrent
 * vers le composant générique `<DataTable>`. Les spécificités (sticky header,
 * footnote, sort direction par colonne) sont gérées autour du composant —
 * `<DataTable>` gère nativement le tri, le rendu des cellules et le click.
 */

import { useMemo } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { Target } from 'lucide-react';
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
  const list = useMemo(() => {
    return Array.isArray(plans) ? [...plans] : [];
  }, [plans]);

  const selectedKey = selectedPlan ? planKey(selectedPlan) : null;

  // Colonnes pour <DataTable> — triables sur score_min, rr, gain_pct,
  // distance_pct, signal_time. Les autres colonnes (Statut, Sens, Setup,
  // Entrée, SL, TP, Déclencheur) sont non triables (noSort: true).
  const columns: DataTableColumn<TradePlan>[] = [
    {
      key: 'signal_time',
      header: 'Signal',
      align: 'left',
      sortValue: (p) => p.signal_time ?? 0,
      render: (p) => (
        <span className="font-mono whitespace-nowrap text-muted">
          {formatSignalTime(p.signal_time) ?? '—'}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Statut',
      noSort: true,
      render: (p) => {
        const st = STATUS_LABEL[String(p.status ?? '')] ?? {
          label: p.status || '—', variant: 'muted' as const,
        };
        return <Badge variant={st.variant}>{st.label}</Badge>;
      },
    },
    {
      key: 'side',
      header: 'Sens',
      noSort: true,
      render: (p) => {
        const isLong = p.side === 'long';
        return (
          <span className={cn('font-semibold', isLong ? 'text-emerald-400' : 'text-red-400')}>
            {p.side?.toUpperCase() || '—'}
          </span>
        );
      },
    },
    {
      key: 'setup',
      header: 'Setup',
      noSort: true,
      render: (p) => <span className="text-cyan-400 font-mono">{p.setup || '—'}</span>,
    },
    {
      key: 'entry',
      header: 'Entrée',
      align: 'right',
      noSort: true,
      render: (p) => <span className="font-mono">{fmtPrice(p.entry)}</span>,
    },
    {
      key: 'stop',
      header: 'SL',
      align: 'right',
      noSort: true,
      render: (p) => <span className="font-mono text-red-400">{fmtPrice(p.stop)}</span>,
    },
    {
      key: 'tp',
      header: 'TP',
      align: 'right',
      noSort: true,
      render: (p) => <span className="font-mono text-emerald-400">{fmtPrice(p.tp)}</span>,
    },
    {
      key: 'gain_pct',
      header: 'Gain',
      align: 'right',
      sortValue: (p) => Number(p.gain_pct ?? 0),
      render: (p) => (
        <span className="font-mono">
          {Number.isFinite(Number(p.gain_pct)) ? `${Number(p.gain_pct).toFixed(2)}%` : '—'}
        </span>
      ),
    },
    {
      key: 'rr',
      header: 'RR',
      align: 'right',
      sortValue: (p) => Number(p.rr ?? 0),
      render: (p) => (
        <span className={cn('font-mono', Number(p.rr ?? 0) >= 2 ? 'text-emerald-400' : 'text-muted')}>
          {Number.isFinite(Number(p.rr)) ? Number(p.rr).toFixed(2) : '—'}
        </span>
      ),
    },
    {
      key: 'distance_pct',
      header: 'Dist',
      align: 'right',
      sortValue: (p) => Number(p.distance_pct ?? 0),
      render: (p) => (
        <span className="font-mono text-muted">
          {Number.isFinite(Number(p.distance_pct)) ? `${Number(p.distance_pct).toFixed(2)}%` : '—'}
        </span>
      ),
    },
    {
      key: 'score_min',
      header: 'Score',
      align: 'right',
      sortValue: (p) => Number(p.score_min ?? 0),
      render: (p) => (
        <span className="font-mono">
          {Number.isFinite(Number(p.score_min)) ? Number(p.score_min).toFixed(2) : '—'}
        </span>
      ),
    },
    {
      key: 'trigger',
      header: 'Déclencheur',
      noSort: true,
      render: (p) => (
        <span className="text-muted truncate max-w-[16rem] block" title={p.trigger || p.reason || ''}>
          {p.trigger || p.reason || '—'}
        </span>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Target className="w-3.5 h-3.5" />
          {title} ({list.length})
        </CardTitle>
        {onSelectPlan && (
          <p className="text-[11px] text-muted font-normal">
            Cliquez une ligne pour afficher Entry / SL / TP sur le graphique
          </p>
        )}
      </CardHeader>
      <CardContent className="p-0">
        <div className="max-h-96 overflow-y-auto">
          <DataTable
            columns={columns}
            rows={list}
            sortable
            initialSortKey="signal_time"
            initialSortAsc={false}
            onRowClick={onSelectPlan}
            rowKey={(p, i) => planKey(p) || `plan-${i}`}
            emptyLabel="Aucun trade recommandé pour cette paire / TF"
          />
        </div>
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
 *
 * F1 (refactor) : utilise désormais le composant générique `<DataTable>`.
 * Les colonnes triables sont signalées via `noSort: false` (défaut), les
 * autres via `noSort: true`. Le tri et la sélection de ligne sont gérés
 * nativement par DataTable.
 */
export function RealizedTradesTable({
  trades,
  title,
  strategy = 'smart_money',
  onSelectTrade,
  selectedTrade,
  footnote,
}: {
  trades?: RealizedTrade[] | null;
  /** Si omis : « Trades réalisés (N — Backtest {strategy}) ». */
  title?: string;
  /** Stratégie utilisée pour le backtest (affichée dans le titre). */
  strategy?: string;
  /** Clic ligne → Entry / SL / TP sur le graphique (comme Trades recommandés). */
  onSelectTrade?: (trade: RealizedTrade) => void;
  selectedTrade?: RealizedTrade | null;
  /** Note explicative rendue directement sous le tableau, dans le bloc. */
  footnote?: React.ReactNode;
}) {
  const list = useMemo(() => {
    const arr = Array.isArray(trades) ? [...trades] : [];
    return arr.filter(
      (t) => t.exit != null || t.exit_price != null || t.exit_bar != null,
    );
  }, [trades]);

  const closed = list.length;
  const wins = list.filter((t) => Number(t.pnl ?? 0) > 0).length;
  const displayTitle = title ?? `Trades réalisés (${closed} — Backtest ${strategy})`;

  const selectedKey = selectedTrade
    ? `${selectedTrade.side}|${selectedTrade.setup}|${selectedTrade.entry ?? selectedTrade.entry_price}|${selectedTrade.signal_time}`
    : null;

  // Colonnes pour <DataTable>
  const columns: DataTableColumn<RealizedTrade>[] = [
    {
      key: 'signal_time',
      header: 'Signal',
      align: 'left',
      sortValue: (t) => realizedSignalTime(t) ?? 0,
      render: (t) => (
        <span className="font-mono whitespace-nowrap text-muted">
          {formatSignalTime(realizedSignalTime(t)) ?? '—'}
        </span>
      ),
    },
    {
      key: 'result',
      header: 'Résultat',
      noSort: true,
      render: (t) => {
        const pnl = Number(t.pnl ?? 0);
        const win = pnl > 0;
        const loss = pnl < 0;
        return (
          <Badge variant={win ? 'success' : loss ? 'danger' : 'muted'}>
            {win ? 'Gagnant' : loss ? 'Perdant' : '—'}
          </Badge>
        );
      },
    },
    {
      key: 'side',
      header: 'Sens',
      noSort: true,
      render: (t) => {
        const isLong = t.side === 'long';
        return (
          <span className={cn('font-semibold', isLong ? 'text-emerald-400' : 'text-red-400')}>
            {String(t.side || '—').toUpperCase()}
          </span>
        );
      },
    },
    {
      key: 'setup',
      header: 'Setup',
      noSort: true,
      render: (t) => <span className="text-cyan-400 font-mono">{t.setup || '—'}</span>,
    },
    {
      key: 'entry',
      header: 'Entrée',
      align: 'right',
      noSort: true,
      render: (t) => <span className="font-mono">{fmtPrice(t.entry ?? t.entry_price)}</span>,
    },
    {
      key: 'stop',
      header: 'SL',
      align: 'right',
      noSort: true,
      render: (t) => <span className="font-mono text-red-400">{fmtPrice(t.stop)}</span>,
    },
    {
      key: 'tp',
      header: 'TP',
      align: 'right',
      noSort: true,
      render: (t) => (
        <span className="font-mono text-emerald-400">{fmtPrice(t.tp ?? t.take_profit)}</span>
      ),
    },
    {
      key: 'gain_pct',
      header: 'Gain espéré',
      align: 'right',
      sortValue: (t) => expectedGainPct(t) ?? 0,
      render: (t) => {
        const gain = expectedGainPct(t);
        return <span className="font-mono">{gain != null ? `${gain.toFixed(2)}%` : '—'}</span>;
      },
    },
    {
      key: 'pnl_pct',
      header: 'PnL%',
      align: 'right',
      sortValue: (t) => Number(t.pnl_pct ?? t.pnl ?? 0),
      render: (t) => {
        const pnl = Number(t.pnl ?? 0);
        const pct = t.pnl_pct != null ? Number(t.pnl_pct) : null;
        const win = pnl > 0;
        const loss = pnl < 0;
        return (
          <span className={cn(
            'font-mono font-semibold',
            win ? 'text-emerald-400' : loss ? 'text-red-400' : 'text-muted',
          )}>
            {pct != null && Number.isFinite(pct)
              ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`
              : pnl !== 0
                ? formatUSD(pnl, { sign: true })
                : '—'}
          </span>
        );
      },
    },
    {
      key: 'rr',
      header: 'RR',
      align: 'right',
      sortValue: (t) => expectedRr(t) ?? 0,
      render: (t) => {
        const rr = expectedRr(t);
        return (
          <span className={cn('font-mono', (rr ?? 0) >= 2 ? 'text-emerald-400' : 'text-muted')}>
            {rr != null ? rr.toFixed(2) : '—'}
          </span>
        );
      },
    },
    {
      key: 'distance_pct',
      header: 'Dist',
      align: 'right',
      noSort: true,
      render: (t) => (
        <span className="font-mono text-muted">
          {t.distance_pct != null && Number.isFinite(Number(t.distance_pct))
            ? `${Number(t.distance_pct).toFixed(2)}%`
            : '—'}
        </span>
      ),
    },
    {
      key: 'score_min',
      header: 'Score',
      align: 'right',
      sortValue: (t) => Number(t.score_min ?? t.score ?? 0),
      render: (t) => {
        const score = t.score_min ?? t.score;
        return (
          <span className="font-mono">
            {score != null && Number.isFinite(Number(score)) ? Number(score).toFixed(2) : '—'}
          </span>
        );
      },
    },
    {
      key: 'reason',
      header: 'Raison',
      noSort: true,
      render: (t) => {
        const reason = t.exit_reason || t.reason || '';
        return (
          <span className="text-muted truncate max-w-[16rem] block" title={reason}>
            {reason || '—'}
          </span>
        );
      },
    },
  ];

  // Wrapper pour onRowClick : si la ligne cliquée correspond au selectedTrade,
  // on garde la sélection visible via rowKey.
  const handleRowClick = onSelectTrade
    ? (row: RealizedTrade) => onSelectTrade(row)
    : undefined;

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
            {onSelectTrade ? ' · Cliquez une ligne pour Entry / SL / TP' : ''}
          </span>
        )}
      </CardHeader>
      <CardContent className="p-0">
        {closed === 0 ? (
          <p className="text-xs text-muted p-4 text-center">Aucun trade réalisé</p>
        ) : (
          <div className="max-h-96 overflow-y-auto">
            <DataTable
              columns={columns}
              rows={list}
              sortable
              initialSortKey="signal_time"
              initialSortAsc={false}
              onRowClick={handleRowClick}
              rowKey={(t, i) =>
                `${t.side}|${t.setup}|${t.entry ?? t.entry_price}|${t.signal_time ?? i}`
              }
              emptyLabel="Aucun trade réalisé"
            />
          </div>
        )}
        {footnote && (
          <div className="text-[11px] text-dim p-3 pt-2 border-t border-border/50">
            {footnote}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
