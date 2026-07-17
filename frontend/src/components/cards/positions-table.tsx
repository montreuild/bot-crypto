'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, formatPct, timeAgo } from '@/lib/utils';
import { useBotStatus } from '@/hooks/use-api';
import { ArrowUpCircle, ArrowDownCircle, Clock } from 'lucide-react';
import { useState, useEffect } from 'react';

export function PositionsTable() {
  const { data: status } = useBotStatus();
  const positions = status?.positions || [];
  const [tick, setTick] = useState(0);

  // Re-render toutes les 5s pour updater le "il y a X"
  useEffect(() => {
    const i = setInterval(() => setTick((t) => t + 1), 5000);
    return () => clearInterval(i);
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Positions Ouvertes</CardTitle>
        <Badge variant={positions.length > 0 ? 'info' : 'default'}>
          {positions.length} active{positions.length > 1 ? 's' : ''}
        </Badge>
      </CardHeader>
      <CardContent>
        {positions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="w-12 h-12 rounded-full bg-card-hover flex items-center justify-center mb-3">
              <Clock className="w-6 h-6 text-dim" />
            </div>
            <div className="text-sm text-muted">Aucune position ouverte</div>
            <div className="text-xs text-dim mt-1">En attente de signaux...</div>
          </div>
        ) : (
          <div className="space-y-2">
            {positions.map((pos) => {
              const pnlPositive = pos.upnl >= 0;
              const SideIcon = pos.side === 'long' ? ArrowUpCircle : ArrowDownCircle;
              return (
                <div
                  key={pos.id}
                  className={cn(
                    'flex items-center gap-3 p-3 rounded-lg border border-border bg-card-hover hover:border-border-hi transition-all',
                    pnlPositive && 'hover:bg-emerald-500/5',
                    !pnlPositive && 'hover:bg-red-500/5',
                  )}
                >
                  <SideIcon
                    className={cn(
                      'w-5 h-5 flex-shrink-0',
                      pos.side === 'long' ? 'text-emerald-400' : 'text-red-400',
                    )}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm">{pos.symbol}</span>
                      <Badge variant={pos.side === 'long' ? 'success' : 'danger'}>
                        {pos.side.toUpperCase()}
                      </Badge>
                      <span className="text-xs text-muted">{pos.strategy}</span>
                      <span className="text-xs text-dim">·</span>
                      <span className="text-xs text-muted font-mono">{pos.timeframe}</span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-muted font-mono">
                      <span>Entry: ${pos.entry.toFixed(2)}</span>
                      <span>Stop: ${pos.stop.toFixed(2)}</span>
                      <span>Size: {pos.size.toFixed(6)}</span>
                      <span className="text-dim">· {timeAgo(pos.open_time)} ago</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={cn('font-mono font-semibold text-sm', pnlPositive ? 'text-emerald-400' : 'text-red-400')}>
                      {pnlPositive ? '+' : ''}{formatUSD(pos.upnl)}
                    </div>
                    <div className="text-xs text-dim font-mono">
                      {pnlPositive ? '+' : ''}{((pos.upnl / pos.notional) * 100).toFixed(2)}%
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
