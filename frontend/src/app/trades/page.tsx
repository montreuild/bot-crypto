'use client';

import { useTrades } from '@/hooks/use-api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatPct, formatDateTime } from '@/lib/utils';
import { api } from '@/lib/api';
import { Download, ArrowUp, ArrowDown } from 'lucide-react';
import { useState } from 'react';

export default function TradesPage() {
  const [limit, setLimit] = useState(100);
  const [symbolFilter, setSymbolFilter] = useState('');
  const [strategyFilter, setStrategyFilter] = useState('');
  const { data, isLoading } = useTrades({ limit, symbol: symbolFilter || undefined, strategy: strategyFilter || undefined });

  const trades = data?.trades || [];
  const total = data?.total || 0;

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Trades</h1>
          <p className="text-sm text-muted mt-1">{total} trades au total</p>
        </div>
        <a href={api.exportTradesCsv()}>
          <Button variant="outline" size="sm">
            <Download className="w-4 h-4" />
            Export CSV
          </Button>
        </a>
      </div>

      {/* Filters */}
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
                    <td colSpan={10} className="p-8 text-center text-muted">
                      Chargement...
                    </td>
                  </tr>
                ) : trades.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="p-8 text-center text-muted">
                      Aucun trade
                    </td>
                  </tr>
                ) : (
                  trades.map((trade) => {
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
