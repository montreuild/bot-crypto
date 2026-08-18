'use client';

// UI-04 fix (Sprint 5) : filtre Slot en 3 parties (strategy::tf::symbol),
// aligné sur bots.html / portfolio.html. Le filtre regroupe maintenant
// les bots distincts (un slot par stratégie+TF+symbole) au lieu de les
// mélanger sous une clé 2-parties.
//
// F1 (refactor final) : la table inline migre vers `<DataTable>` (5e et
// dernière table spécialisée absorbée). Les filtres (Symbole, Stratégie,
// TF, Slot, Limite) restent en dehors du composant — `<DataTable>` gère
// uniquement le rendu des lignes et le tri par colonne.

import { useTrades } from '@/hooks/use-api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { cn, formatMoney, formatPct, formatDateTime, quoteCurrency } from '@/lib/utils';
import { api } from '@/lib/api';
import { Download, ArrowUp, ArrowDown } from 'lucide-react';
import { useState, useMemo } from 'react';
import type { Trade } from '@/types';

export default function TradesPage() {
  const [limit, setLimit] = useState(100);
  const [symbolFilter, setSymbolFilter] = useState('');
  const [strategyFilter, setStrategyFilter] = useState('');
  const [tfFilter, setTfFilter] = useState('');
  // UI-04 : filtre slot en 3 parties (strategy::tf::symbol) pour ne plus
  // mélanger les symboles sous une clé 2-parties.
  const [slotFilter, setSlotFilter] = useState('');
  const { data, isLoading } = useTrades({ limit, symbol: symbolFilter || undefined, strategy: strategyFilter || undefined });

  // Mémoïsé : `data?.trades || []` crée un tableau neuf à chaque render, ce qui
  // invalidait les useMemo de filtrage/agrégation en aval à chaque tick du
  // sondage (15 s) même quand les trades n'avaient pas changé.
  const trades = useMemo(() => data?.trades || [], [data]);
  const total = data?.total || 0;

  // Filtrage par slot 3-parties en client (l'API ne supporte pas slot=...)
  // Construit la clé 3-parties pour chaque trade puis filtre par slotFilter.
  const filteredTrades = useMemo(() => {
    if (!slotFilter && !tfFilter) return trades;
    return trades.filter((t) => {
      // Slot key 3-parties : strategy::timeframe::symbol
      // UI-04 : timeframe peut être absent des anciens trades (avant la
      // colonne TF) — on repli sur chaîne vide pour ne pas crasher.
      const tf = t.timeframe || '';
      const slot = `${t.strategy}::${tf}::${t.symbol}`;
      if (slotFilter && !slot.toLowerCase().includes(slotFilter.toLowerCase())) {
        return false;
      }
      if (tfFilter && tf !== tfFilter) {
        return false;
      }
      return true;
    });
  }, [trades, slotFilter, tfFilter]);

  // Liste des slots uniques (pour l'autocomplétion)
  const availableSlots = useMemo(() => {
    const slots = new Set<string>();
    trades.forEach((t) => {
      const tf = t.timeframe || '';
      slots.add(`${t.strategy}::${tf}::${t.symbol}`);
    });
    return Array.from(slots).sort();
  }, [trades]);

  // Colonnes pour <DataTable> — 11 colonnes, triables sur les principales.
  const columns: DataTableColumn<Trade>[] = [
    {
      key: 'time',
      header: 'Time',
      align: 'left',
      sortValue: (t) => t.time,
      render: (t) => (
        <span className="text-xs text-muted font-mono">{formatDateTime(t.time)}</span>
      ),
    },
    {
      key: 'symbol',
      header: 'Symbol',
      sortValue: (t) => t.symbol,
      render: (t) => <span className="font-semibold">{t.symbol}</span>,
    },
    {
      key: 'side',
      header: 'Side',
      noSort: true,
      render: (t) => {
        const isLong = t.side === 'long';
        return (
          <span className={cn('inline-flex items-center gap-1 text-xs font-semibold', isLong ? 'text-emerald-400' : 'text-red-400')}>
            {isLong ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />}
            {t.side.toUpperCase()}
          </span>
        );
      },
    },
    {
      key: 'strategy',
      header: 'Strategy',
      sortValue: (t) => t.strategy,
      render: (t) => <span className="text-xs text-muted font-mono">{t.strategy}</span>,
    },
    {
      key: 'timeframe',
      header: 'TF',
      sortValue: (t) => t.timeframe || '',
      render: (t) => <span className="text-xs text-dim font-mono">{t.timeframe || '—'}</span>,
    },
    {
      key: 'entry',
      header: 'Entry',
      align: 'right',
      sortValue: (t) => t.entry,
      render: (t) => <span className="font-mono">${t.entry.toFixed(2)}</span>,
    },
    {
      key: 'exit',
      header: 'Exit',
      align: 'right',
      sortValue: (t) => t.exit,
      render: (t) => <span className="font-mono">${t.exit.toFixed(2)}</span>,
    },
    {
      key: 'pnl',
      header: 'PnL',
      align: 'right',
      sortValue: (t) => t.pnl,
      render: (t) => {
        const isWin = t.pnl >= 0;
        return (
          <span className={cn('font-mono font-semibold', isWin ? 'text-emerald-400' : 'text-red-400')}>
            {isWin ? '+' : ''}{formatMoney(t.pnl, quoteCurrency(t))}
          </span>
        );
      },
    },
    {
      key: 'pnl_pct',
      header: 'PnL %',
      align: 'right',
      sortValue: (t) => t.pnl_pct,
      render: (t) => {
        const isWin = t.pnl >= 0;
        return (
          <span className={cn('font-mono', isWin ? 'text-emerald-400' : 'text-red-400')}>
            {isWin ? '+' : ''}{formatPct(t.pnl_pct)}
          </span>
        );
      },
    },
    {
      key: 'fees',
      header: 'Fees',
      align: 'right',
      sortValue: (t) => t.fees,
      render: (t) => <span className="font-mono text-xs text-dim">-{formatMoney(t.fees, quoteCurrency(t))}</span>,
    },
    {
      key: 'reason',
      header: 'Reason',
      noSort: true,
      render: (t) => <span className="text-xs text-muted">{t.reason}</span>,
    },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Trades</h1>
          <p className="text-sm text-muted mt-1">
            {total} trades au total · {filteredTrades.length} affichés
          </p>
        </div>
        <a href={api.exportTradesCsv()}>
          <Button variant="outline" size="sm">
            <Download className="w-4 h-4" />
            Export CSV
          </Button>
        </a>
      </div>

      {/* Filters — UI-04 : filtre Slot 3-parties ajouté */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div>
            <label className="text-xs text-dim block mb-1">Symbole</label>
            <input
              aria-label="Symbole"
              type="text"
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value)}
              placeholder="BTC/USDC"
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-sm w-40"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1">Stratégie</label>
            <input
              aria-label="Stratégie"
              type="text"
              value={strategyFilter}
              onChange={(e) => setStrategyFilter(e.target.value)}
              placeholder="trend_rider"
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-sm w-40"
            />
          </div>
          <div>
            <label className="text-xs text-dim block mb-1">Timeframe</label>
            <select
              aria-label="Timeframe"
              value={tfFilter}
              onChange={(e) => setTfFilter(e.target.value)}
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-sm"
            >
              <option value="">Toutes</option>
              <option value="15m">15m</option>
              <option value="30m">30m</option>
              <option value="1h">1h</option>
              <option value="4h">4h</option>
              <option value="1d">1d</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-dim block mb-1">
              Slot (strategy::tf·paire) <Badge variant="info" className="ml-1 text-[10px]">UI-04</Badge>
            </label>
            <input
              type="text"
              value={slotFilter}
              onChange={(e) => setSlotFilter(e.target.value)}
              placeholder="trend_rider::1h::BTC/USDC"
              list="available-slots"
              className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-sm w-72 font-mono text-xs"
            />
            <datalist id="available-slots">
              {availableSlots.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
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
              <option value={500}>500</option>
              <option value={1000}>1000</option>
            </select>
          </div>
        </CardContent>
      </Card>

      {/* Table — F1 : utilise <DataTable> */}
      <Card>
        <CardContent className="p-3">
          {isLoading ? (
            <div className="p-8 text-center text-muted text-sm">Chargement...</div>
          ) : (
            <DataTable
              columns={columns}
              rows={filteredTrades}
              sortable
              paginated
              pageSize={50}
              initialSortKey="time"
              initialSortAsc={false}
              rowKey={(t) => String(t.id ?? `${t.symbol}-${t.time}`)}
              emptyLabel={slotFilter ? `Aucun trade (slot: ${slotFilter})` : 'Aucun trade'}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
