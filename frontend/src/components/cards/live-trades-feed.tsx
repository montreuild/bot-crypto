'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useTradeEvents } from '@/lib/ws-provider';
import { cn, formatUSD, formatPct, formatTime } from '@/lib/utils';
import { ArrowUp, ArrowDown, TrendingUp, TrendingDown } from 'lucide-react';

export function LiveTradesFeed() {
  const { openedTrades, closedTrades } = useTradeEvents();

  // Combine et trie par timestamp desc
  const allEvents = [
    ...openedTrades.map((e) => ({ ...e, kind: 'opened' as const })),
    ...closedTrades.map((e) => ({ ...e, kind: 'closed' as const })),
  ].sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 20);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          Trades Live
        </CardTitle>
        <Badge variant="success">{openedTrades.length}</Badge>
      </CardHeader>
      <CardContent>
        {allEvents.length === 0 ? (
          <div className="text-center py-8 text-sm text-muted">
            Aucun trade temps réel pour le moment.
            <div className="text-xs text-dim mt-2">
              Les ouvertures/fermetures apparaîtront ici dès qu&apos;elles se produisent.
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {allEvents.map((evt, idx) => {
              const data = evt.data as any;
              const isLong = data.side === 'long';
              const isOpened = evt.kind === 'opened';
              const pnlPositive = !isOpened && data.pnl >= 0;

              return (
                <div
                  key={`${evt.ts}-${idx}`}
                  className={cn(
                    'flex items-center gap-3 p-3 rounded-lg border-l-2 bg-card-hover',
                    isOpened
                      ? (isLong ? 'border-emerald-400' : 'border-red-400')
                      : (pnlPositive ? 'border-emerald-400' : 'border-red-400'),
                    isOpened && 'animate-slide-down',
                  )}
                >
                  <div className={cn(
                    'p-1.5 rounded-md',
                    isOpened
                      ? (isLong ? 'bg-emerald-500/10' : 'bg-red-500/10')
                      : (pnlPositive ? 'bg-emerald-500/10' : 'bg-red-500/10'),
                  )}>
                    {isOpened ? (
                      isLong ? <ArrowUp className="w-4 h-4 text-emerald-400" /> : <ArrowDown className="w-4 h-4 text-red-400" />
                    ) : (
                      pnlPositive ? <TrendingUp className="w-4 h-4 text-emerald-400" /> : <TrendingDown className="w-4 h-4 text-red-400" />
                    )}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm">
                        {isOpened ? 'OPEN' : 'CLOSE'}
                      </span>
                      <span className="font-semibold">{data.symbol}</span>
                      <Badge variant={isLong ? 'success' : 'danger'}>
                        {data.side.toUpperCase()}
                      </Badge>
                    </div>
                    <div className="text-xs text-muted font-mono mt-0.5">
                      {isOpened ? (
                        <>
                          @ ${data.entry.toFixed(2)} · stop ${data.stop.toFixed(2)} · {data.strategy} {data.timeframe}
                        </>
                      ) : (
                        <>
                          ${data.entry.toFixed(2)} → ${data.exit.toFixed(2)} · {data.duration_bars} bars · {data.reason}
                        </>
                      )}
                    </div>
                  </div>

                  <div className="text-right">
                    {!isOpened && (
                      <>
                        <div className={cn('font-mono font-semibold text-sm', pnlPositive ? 'text-emerald-400' : 'text-red-400')}>
                          {pnlPositive ? '+' : ''}{formatUSD(data.pnl)}
                        </div>
                        <div className={cn('text-xs font-mono', pnlPositive ? 'text-emerald-400' : 'text-red-400')}>
                          {pnlPositive ? '+' : ''}{formatPct(data.pnl_pct)}
                        </div>
                      </>
                    )}
                    <div className="text-[10px] text-dim font-mono mt-1">
                      {formatTime(evt.ts)}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
