'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useSignalEvents } from '@/lib/ws-provider';
import { useBotStatus } from '@/hooks/use-api';
import { cn, formatTime } from '@/lib/utils';
import { CheckCircle2, XCircle, Radio } from 'lucide-react';
import { useEffect, useRef, useState, useMemo } from 'react';

/**
 * S3-F1-US3 — Journal des signaux avec rejets.
 *
 * Ajoute une checkbox « Voir rejets » qui affiche/masque les signaux rejetés
 * (status !== 'opened'/'closed'). Affiche la colonne « Raison » pour comprendre
 * pourquoi un signal a été écarté (seuil, budget, corrélation, risque).
 *
 * Avant : tous les signaux étaient affichés mais sans distinction claire
 * accepté/rejeté, et la raison n'était pas visible.
 */
export function SignalsFeed() {
  const signals = useSignalEvents();
  const { data: status } = useBotStatus();
  const historicalSignals = status?.signal_log || [];
  const feedRef = useRef<HTMLDivElement>(null);
  const [showRejected, setShowRejected] = useState(false);
  const [reasonFilter, setReasonFilter] = useState<string>('all');

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

  // Filtre : masquer rejets si checkbox décochée
  const filteredSignals = useMemo(() => {
    let result = showRejected ? allSignals : allSignals.filter((s) => s.accepted);
    if (reasonFilter !== 'all') {
      result = result.filter((s) => {
        const reason = (s.reason || '').toLowerCase();
        if (reasonFilter === 'threshold') return reason.includes('seuil') || reason.includes('threshold') || reason.includes('score');
        if (reasonFilter === 'budget') return reason.includes('budget') || reason.includes('capital') || reason.includes('allocation');
        if (reasonFilter === 'correlation') return reason.includes('correl') || reason.includes('corrél');
        if (reasonFilter === 'risk') return reason.includes('risk') || reason.includes('risque') || reason.includes('cb') || reason.includes('circuit');
        return true;
      });
    }
    return result;
  }, [allSignals, showRejected, reasonFilter]);

  // Compte des rejets pour affichage
  const rejectedCount = allSignals.filter((s) => !s.accepted).length;

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
        <div className="flex items-center gap-3">
          <Badge variant="info">{filteredSignals.length}</Badge>
          {rejectedCount > 0 && (
            <label className="flex items-center gap-1.5 text-[10px] text-muted cursor-pointer">
              <input
                type="checkbox"
                checked={showRejected}
                onChange={(e) => setShowRejected(e.target.checked)}
                className="rounded border-border"
                aria-label="Voir les signaux rejetés"
              />
              <span>Voir rejets ({rejectedCount})</span>
            </label>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {/* Filtre par raison (visible seulement si showRejected) */}
        {showRejected && rejectedCount > 0 && (
          <div className="mb-3 flex items-center gap-2 text-[10px]">
            <span className="text-dim uppercase tracking-wider">Filtre raison:</span>
            {['all', 'threshold', 'budget', 'correlation', 'risk'].map((r) => (
              <button
                key={r}
                onClick={() => setReasonFilter(r)}
                className={cn(
                  'px-2 py-0.5 rounded border transition-colors',
                  reasonFilter === r
                    ? 'bg-primary-500/10 border-primary-400 text-primary-400'
                    : 'border-border text-muted hover:border-border-hi',
                )}
              >
                {r === 'all' ? 'Tous' : r === 'threshold' ? 'Seuil' : r === 'budget' ? 'Budget' : r === 'correlation' ? 'Corrél.' : 'Risque'}
              </button>
            ))}
          </div>
        )}

        <div ref={feedRef} className="space-y-1.5 max-h-96 overflow-y-auto no-scrollbar">
          {filteredSignals.length === 0 ? (
            <div className="text-center py-8 text-sm text-muted">
              {showRejected ? 'Aucun signal à afficher' : 'En attente de signaux acceptés...'}
            </div>
          ) : (
            filteredSignals.map((sig, idx) => {
              const Icon = sig.accepted ? CheckCircle2 : XCircle;
              const sideColor = sig.side === 'long' ? 'text-emerald-400' : sig.side === 'short' ? 'text-red-400' : 'text-muted';
              return (
                <div
                  key={`${sig.time}-${idx}`}
                  className={cn(
                    'p-2 rounded-md text-xs font-mono',
                    sig.source === 'ws' && 'bg-primary-500/5 border border-primary-500/20',
                    !sig.accepted && 'opacity-70',
                  )}
                >
                  <div className="flex items-center gap-2">
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
                  {/* S3-F1-US3 — Raison du rejet affichée si rejeté */}
                  {!sig.accepted && sig.reason && (
                    <div className="mt-1 ml-6 text-[10px] text-amber-400/80 italic">
                      ↳ {sig.reason}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </CardContent>
    </Card>
  );
}
