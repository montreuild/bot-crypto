/**
 * BT-002 — Tableau des trades (sortable, paginé, filtrable, expandable).
 *
 * Extrait du template Jinja2 `backtest.html:770-981` (`buildTrades`).
 *
 * 14 colonnes, filtres (Tous/Long/Short/Win/Loss), tri par colonne,
 * pagination 20/page, lignes expandables (raison/conditions/indicateurs/sizing/position).
 */

import { useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronUp, Download, ChevronRight } from 'lucide-react';
import { exitReasonBadge } from '@/lib/exit-reason-badges';
import { downloadTradesCsv } from '@/lib/trades-csv';
import { normalizeTrade } from '@/lib/backend-normalizers';
import type { BacktestTrade } from '@/types';

interface Props {
  trades: BacktestTrade[];
  pageSize?: number;
  meta?: { symbol: string; timeframe: string; strategy: string };
  onRowClick?: (trade: BacktestTrade) => void;
}

type Filter = 'all' | 'long' | 'short' | 'win' | 'loss';
type SortKey = 'id' | 'entry_time' | 'entry' | 'exit' | 'duration_bars' | 'score' | 'pnl' | 'pnl_pct' | 'fees';

const PG = 20;

function fmtNum(v: number | null | undefined, decimals = 4, prefix = '', suffix = ''): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${prefix}${v.toFixed(decimals)}${suffix}`;
}

function fmtSigned(v: number | null, decimals = 4, prefix = '', suffix = ''): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${prefix}${v.toFixed(decimals)}${suffix}`;
}

function fmtDuration(bars: number | null | undefined): string {
  if (bars == null) return '—';
  if (bars < 24) return `${bars}b`;
  const days = Math.floor(bars / 24);
  const hours = bars % 24;
  return `${bars}b (${days}d ${hours}h)`;
}

export function TradesTable({ trades, meta, onRowClick }: Props) {
  const [filter, setFilter] = useState<Filter>('all');
  const [sortKey, setSortKey] = useState<SortKey>('entry_time');
  const [sortAsc, setSortAsc] = useState(false);
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<string | number | null>(null);

  // Normalise les trades (alias backend → frontend).
  const normalized = useMemo(() => trades.map(normalizeTrade), [trades]);

  const hasSetup = useMemo(() => normalized.some((t) => t.setup), [normalized]);

  // Filtres.
  const filtered = useMemo(() => {
    return normalized.filter((t) => {
      if (filter === 'long') return t.side === 'long';
      if (filter === 'short') return t.side === 'short';
      if (filter === 'win') return (t.pnl ?? 0) > 0;
      if (filter === 'loss') return (t.pnl ?? 0) <= 0;
      return true;
    });
  }, [normalized, filter]);

  // Tri.
  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[sortKey];
      const bv = (b as unknown as Record<string, unknown>)[sortKey];
      let avn: number | string = av == null ? -Infinity : (av as number | string);
      let bvn: number | string = bv == null ? -Infinity : (bv as number | string);
      if (typeof avn === 'string') {
        avn = avn.toLowerCase();
        bvn = (bvn as string)?.toLowerCase?.() ?? '';
      }
      if (avn < bvn) return sortAsc ? -1 : 1;
      if (avn > bvn) return sortAsc ? 1 : -1;
      return 0;
    });
    return arr;
  }, [filtered, sortKey, sortAsc]);

  // Pagination.
  const totalPages = Math.max(1, Math.ceil(sorted.length / PG));
  const currentPage = Math.min(page, totalPages);
  const slice = sorted.slice((currentPage - 1) * PG, currentPage * PG);

  // Compteurs pour les chips de filtre.
  const counts = useMemo(() => {
    const longs = normalized.filter((t) => t.side === 'long').length;
    const shorts = normalized.filter((t) => t.side === 'short').length;
    const wins = normalized.filter((t) => (t.pnl ?? 0) > 0).length;
    const losses = normalized.filter((t) => (t.pnl ?? 0) <= 0).length;
    return { all: normalized.length, longs, shorts, wins, losses };
  }, [normalized]);

  const handleSort = (k: SortKey) => {
    if (sortKey === k) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(k);
      setSortAsc(false);
    }
    setPage(1);
  };

  const th = (k: SortKey, label: string) => (
    <th
      className="text-left py-1 px-2 cursor-pointer select-none hover:bg-muted/30"
      onClick={() => handleSort(k)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {sortKey === k && (
          <span className="text-cyan-400">{sortAsc ? '▲' : '▼'}</span>
        )}
      </span>
    </th>
  );

  const handleExport = () => {
    if (!meta) return;
    downloadTradesCsv(normalized, meta);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">📋 Trades ({normalized.length})</CardTitle>
          {meta && (
            <Button variant="ghost" size="sm" onClick={handleExport} className="h-7 text-xs">
              <Download className="h-3 w-3 mr-1" /> CSV
            </Button>
          )}
        </div>
        <div className="flex flex-wrap gap-1 mt-2">
          {(
            [
              ['all', `Tous (${counts.all})`],
              ['long', `Long (${counts.longs})`],
              ['short', `Short (${counts.shorts})`],
              ['win', `✅ Gains (${counts.wins})`],
              ['loss', `🔴 Pertes (${counts.losses})`],
            ] as Array<[Filter, string]>
          ).map(([f, label]) => (
            <button
              key={f}
              type="button"
              onClick={() => {
                setFilter(f);
                setPage(1);
              }}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                filter === f
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                  : 'bg-surface border border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left py-1 px-2">#</th>
                <th className="text-left py-1 px-2">Dir.</th>
                {hasSetup && <th className="text-left py-1 px-2">Setup</th>}
                {th('entry_time', 'Entrée')}
                {th('entry', 'Prix E.')}
                {th('exit', 'Prix S.')}
                {th('duration_bars', 'Dur.')}
                <th className="text-left py-1 px-2">Sortie</th>
                {th('score', 'Score')}
                {th('pnl', 'PnL')}
                {th('pnl_pct', '% PnL')}
                {th('fees', 'Frais')}
                <th className="text-left py-1 px-2"></th>
              </tr>
            </thead>
            <tbody>
              {slice.map((t, i) => {
                const isWin = (t.pnl ?? 0) >= 0;
                const expanded = expandedId === (t.id ?? `${i}-${t.entry_time ?? ''}`);
                const badge = exitReasonBadge(t.exit_reason);
                const rowKey = t.id ?? `${i}-${t.entry_time ?? ''}`;
                const scorePct = Math.round((t.score ?? 0) * 100);
                const scoreColor = (t.score ?? 0) >= 0.7 ? '#34d399' : (t.score ?? 0) >= 0.55 ? '#ffd166' : '#5a7099';
                return (
                  <>
                    <tr
                      key={rowKey}
                      className="border-b border-border/50 hover:bg-muted/20 cursor-pointer"
                      onClick={() => onRowClick?.(t)}
                    >
                      <td className="py-1 px-2 text-muted-foreground">{t.id ?? '—'}</td>
                      <td className="py-1 px-2">
                        <Badge variant={t.side === 'long' ? 'success' : 'danger'} className="text-[0.6rem]">
                          {t.side?.toUpperCase()}
                        </Badge>
                      </td>
                      {hasSetup && (
                        <td className="py-1 px-2 font-mono text-[0.65rem] text-purple-400">{t.setup || '—'}</td>
                      )}
                      <td className="py-1 px-2 text-muted-foreground text-[0.65rem]">
                        {(t.entry_time ?? t.time ?? '').slice(0, 16)}
                      </td>
                      <td className="py-1 px-2 font-mono">{fmtNum(t.entry, 4)}</td>
                      <td className="py-1 px-2 font-mono">{fmtNum(t.exit, 4)}</td>
                      <td className="py-1 px-2 text-muted-foreground">{fmtDuration(t.duration_bars)}</td>
                      <td className="py-1 px-2">
                        <span
                          className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-[0.6rem] font-mono"
                          style={{
                            color: badge.color,
                            background: `${badge.color}15`,
                            border: `1px solid ${badge.color}33`,
                          }}
                        >
                          {badge.emoji} {badge.abbr}
                        </span>
                      </td>
                      <td className="py-1 px-2">
                        <div className="flex items-center gap-1">
                          <div className="w-7 h-[3px] bg-border rounded overflow-hidden">
                            <div
                              className="h-full rounded"
                              style={{ width: `${scorePct}%`, background: scoreColor }}
                            />
                          </div>
                          <span className="text-[0.6rem] text-muted-foreground font-mono">
                            {fmtNum(t.score, 2)}
                          </span>
                        </div>
                      </td>
                      <td className={`py-1 px-2 font-mono ${isWin ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {fmtSigned(t.pnl, 4, '$')}
                      </td>
                      <td className={`py-1 px-2 font-mono ${isWin ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {fmtSigned(t.pnl_pct, 2, '', '%')}
                      </td>
                      <td className="py-1 px-2 font-mono text-amber-400 text-[0.65rem]">
                        −{fmtNum(t.fees, 4, '$')}
                      </td>
                      <td className="py-1 px-2">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedId(expanded ? null : rowKey);
                          }}
                          className="text-muted-foreground hover:text-foreground"
                          aria-label={expanded ? 'Réduire' : 'Étendre'}
                        >
                          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                        </button>
                      </td>
                    </tr>
                    {expanded && (
                      <tr key={`${rowKey}-detail`}>
                        <td colSpan={hasSetup ? 13 : 12} className="py-2 px-3 bg-surface/50">
                          <TradeDetail trade={t} />
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-3 text-xs">
            <span className="text-muted-foreground font-mono">
              Page {currentPage} de {totalPages}
            </span>
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="sm"
                disabled={currentPage === 1}
                onClick={() => setPage(currentPage - 1)}
                className="h-7"
              >
                ◀ Précédent
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={currentPage === totalPages}
                onClick={() => setPage(currentPage + 1)}
                className="h-7"
              >
                Suivant ▶
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** BT-002 bloc 5 — Ligne expandable en 2 colonnes (signal | sortie & sizing). */
function TradeDetail({ trade }: { trade: BacktestTrade }) {
  const indicators = trade.indicators ?? {};
  const conditions = trade.conditions ?? [];
  const indEntries = Object.entries(indicators).slice(0, 20);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* Colonne gauche — Signal */}
      <div className="space-y-2">
        <div>
          <div className="text-[0.65rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
            Raison du signal
          </div>
          <div className="text-xs">{trade.signal_reason ?? trade.reason ?? '—'}</div>
        </div>
        {conditions.length > 0 && (
          <div>
            <div className="text-[0.65rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
              Conditions
            </div>
            <ul className="space-y-0.5">
              {conditions.map((c, i) => (
                <li key={i} className="text-xs flex items-center gap-2">
                  <span className={c.passed ? 'text-emerald-400' : 'text-rose-400'}>
                    {c.passed ? '✓' : '✗'}
                  </span>
                  <span className="text-muted-foreground">{c.label}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {indEntries.length > 0 && (
          <div>
            <div className="text-[0.65rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
              Indicateurs au signal
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[0.65rem]">
              {indEntries.map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-cyan-400 truncate" title={k}>
                    {k.length > 30 ? `${k.slice(0, 28)}…` : k}
                  </span>
                  <span className="text-muted-foreground">:</span>
                  <span className="text-emerald-400 ml-1">{Number(v).toFixed(6)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Colonne droite — Sortie & Sizing */}
      <div className="space-y-1 text-xs">
        <div className="text-[0.65rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
          Sortie &amp; Sizing
        </div>
        <DetailRow label="Raison de sortie" value={trade.exit_reason ?? trade.reason ?? '—'} />
        {trade.setup_v7 && <DetailRow label="Setup V7" value={trade.setup_v7} />}
        {trade.regime_lbl && <DetailRow label="Régime entrée" value={trade.regime_lbl} />}
        <DetailRow label="Notionnel" value={trade.notional != null ? `$${trade.notional.toFixed(2)}` : '—'} />
        <DetailRow label="Taille" value={trade.size != null ? String(trade.size) : '—'} />
        <DetailRow label="Stop initial" value={trade.stop_initial != null ? `$${trade.stop_initial.toFixed(4)}` : '—'} />
        <DetailRow label="SL (×ATR)" value={trade.sl_atr_mult != null ? String(trade.sl_atr_mult) : '—'} />
        <DetailRow label="TP (×ATR)" value={trade.tp_atr_mult != null ? String(trade.tp_atr_mult) : '—'} />
        <DetailRow label="exit_after_bars" value={trade.exit_after_bars != null ? String(trade.exit_after_bars) : '—'} />
        <DetailRow label="size_factor" value={trade.size_factor != null ? trade.size_factor.toFixed(2) : '—'} />
        <DetailRow
          label="disable_trailing"
          value={
            <Badge variant={trade.disable_trailing ? 'danger' : 'success'} className="text-[0.6rem]">
              {trade.disable_trailing ? 'Désactivé' : 'Actif'}
            </Badge>
          }
        />
        <DetailRow
          label="Statut"
          value={
            <Badge variant={trade.status === 'open' ? 'warning' : 'success'} className="text-[0.6rem]">
              {trade.status === 'open' ? 'Ouvert' : 'Fermé'}
            </Badge>
          }
        />
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono ml-2 text-right">{value}</span>
    </div>
  );
}
