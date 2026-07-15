'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useSignalEvents } from '@/lib/ws-provider';
import { useBotStatus } from '@/hooks/use-api';
import { cn, formatTime } from '@/lib/utils';
import { CheckCircle2, XCircle, Radio } from 'lucide-react';
import { useEffect, useRef } from 'react';

export function SignalsFeed() {
  const signals = useSignalEvents();
  const { data: status } = useBotStatus();
  const historicalSignals = status?.signal_log || [];
  const feedRef = useRef<HTMLDivElement>(null);

  // Combine WS events + historical
  const allSignals = [
    ...signals.map((s) => ({
      time: s.ts,
      symbol: s.data.symbol,
      strategy: s.data.slot_key.split('::')[0],
      timeframe: s.data.timeframe,
      side: s.data.side,
      score: s.data.score,
      accepted: s.data.accepted,
      reason: s.data.reason,
      source: 'ws' as const,
    })),
    ...historicalSignals.map((s) => ({
      time: s.time,
      symbol: s.symbol,
      strategy: s.strategy,
      timeframe: s.timeframe,
      side: s.side,
      score: s.score,
      accepted: s.status === 'opened' || s.status === 'closed',
      reason: s.reason,
      source: 'hist' as const,
    })),
  ].slice(0, 50);

  // Auto-scroll en haut
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = 0;
  }, [signals]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Radio className="w-3 h-3 animate-pulse" />
          Signaux Temps Réel
        </CardTitle>
        <Badge variant="info">{allSignals.length}</Badge>
      </CardHeader>
      <CardContent>
        <div ref={feedRef} className="space-y-1.5 max-h-96 overflow-y-auto no-scrollbar">
          {allSignals.length === 0 ? (
            <div className="text-center py-8 text-sm text-muted">
              En attente de signaux...
            </div>
          ) : (
            allSignals.map((sig, idx) => {
              const Icon = sig.accepted ? CheckCircle2 : XCircle;
              const sideColor = sig.side === 'long' ? 'text-emerald-400' : sig.side === 'short' ? 'text-red-400' : 'text-muted';
              return (
                <div
                  key={`${sig.time}-${idx}`}
                  className={cn(
                    'flex items-center gap-2 p-2 rounded-md text-xs font-mono',
                    sig.source === 'ws' && 'bg-primary-500/5 border border-primary-500/20',
                  )}
                >
                  <Icon className={cn('w-3.5 h-3.5 flex-shrink-0', sig.accepted ? 'text-emerald-400' : 'text-red-400')} />
                  <span className="text-dim">{formatTime(sig.time)}</span>
                  <span className="font-semibold">{sig.symbol}</span>
                  <span className={cn('font-bold', sideColor)}>{sig.side.toUpperCase()}</span>
                  <span className="text-muted">{sig.strategy}</span>
                  <span className="text-dim">{sig.timeframe}</span>
                  <div className="flex-1" />
                  <span className="px-1.5 py-0.5 rounded bg-card-hover text-[10px]">
                    {sig.score.toFixed(2)}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </CardContent>
    </Card>
  );
}
