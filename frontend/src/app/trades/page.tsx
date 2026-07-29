'use client';

// UI-04 fix (Sprint 5) : filtre Slot en 3 parties (strategy::tf::symbol),
// aligné sur bots.html / portfolio.html. Le filtre regroupe maintenant
// les bots distincts (un slot par stratégie+TF+symbole) au lieu de les
// mélanger sous une clé 2-parties.

import { useTrades } from '@/hooks/use-api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatPct, formatDateTime } from '@/lib/utils';
import { api } from '@/lib/api';
import { Download, ArrowUp, ArrowDown } from 'lucide-react';
import { useState, useMemo } from 'react';

export default function TradesPage() {
  const [limit, setLimit] = useState(100);
  const [symbolFilter, setSymbolFilter] = useState('');
  const [strategyFilter, setStrategyFilter] = useState('');
  const [tfFilter, setTfFilter] = useState('');
  // UI-04 : filtre slot en 3 parties (strategy::tf::symbol) pour ne plus
  // mélanger les symboles sous une clé 2-parties.
  const [slotFilter, setSlotFilter] = useState('');
  const { data, isLoading } = useTrades({ limit, symbol: symbolFilter || undefined, strategy: strategyFilter || undefined });

  const trades = data?.trades || [];
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

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-dim border-b border-border">
                  <th className="p-3 font-medium">Time</th>
                  <th className="p-3 font-medium">Symbol</th>
                  <th className="p-3 font-medium">Side</th>
                  <th className="p-3 font-medium">Strategy</th>
                  <th className="p-3 font-medium">TF</th>
                  <th className="p-3 font-medium text-right">Entry</th>
                  <th className="p-3 font-medium text-right">Exit</th>
                  <th className="p-3 font-medium text-right">PnL</th>
                  <th className="p-3 font-medium text-right">PnL %</th>
                  <th className="p-3 font-medium text-right">Fees</th>
                  <th className="p-3 font-medium">Reason</th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr>
                    <td colSpan={11} className="p-8 text-center text-muted">
                      Chargement...
                    </td>
                  </tr>
                ) : filteredTrades.length === 0 ? (
                  <tr>
                    <td colSpan={11} className="p-8 text-center text-muted">
                      Aucun trade {slotFilter && `(slot: ${slotFilter})`}
                    </td>
                  </tr>
                ) : (
                  filteredTrades.map((trade) => {
                    const isLong = trade.side === 'long';
                    const isWin = trade.pnl >= 0;
                    return (
                      <tr key={trade.id} className="border-b border-border/30 hover:bg-card-hover transition-colors">
                        <td className="p-3 text-xs text-muted font-mono">{formatDateTime(trade.time)}</td>
                        <td className="p-3 font-semibold">{trade.symbol}</td>
                        <td className="p-3">
                          <span className={cn('inline-flex items-center gap-1 text-xs font-semibold', isLong ? 'text-emerald-400' : 'text-red-400')}>
                            {isLong ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />}
                            {trade.side.toUpperCase()}
                          </span>
                        </td>
                        <td className="p-3 text-xs text-muted font-mono">{trade.strategy}</td>
                        <td className="p-3 text-xs text-dim font-mono">{trade.timeframe || '—'}</td>
                        <td className="p-3 text-right font-mono">${trade.entry.toFixed(2)}</td>
                        <td className="p-3 text-right font-mono">${trade.exit.toFixed(2)}</td>
                        <td className={cn('p-3 text-right font-mono font-semibold', isWin ? 'text-emerald-400' : 'text-red-400')}>
                          {isWin ? '+' : ''}{formatUSD(trade.pnl)}
                        </td>
                        <td className={cn('p-3 text-right font-mono', isWin ? 'text-emerald-400' : 'text-red-400')}>
                          {isWin ? '+' : ''}{formatPct(trade.pnl_pct)}
                        </td>
                        <td className="p-3 text-right font-mono text-xs text-dim">-{formatUSD(trade.fees)}</td>
                        <td className="p-3 text-xs text-muted">{trade.reason}</td>
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
