'use client';

/**
 * S8-F4-US2 — Widget « Top opportunités ».
 *
 * Consomme /api/scanner/opportunities pour afficher le top 10 des paires
 * par score combiné (40% volume 24h + 60% ATR%).
 *
 * Clique sur une paire → redirige vers /market?tab=scanner&symbol=X
 * ou vers /lab?tab=backtest&symbol=X pour analyser.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Loader2, ArrowRight, TrendingUp } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';

interface OpportunitiesWidgetProps {
  timeframe?: string;
  limit?: number;
}

export function OpportunitiesWidget({ timeframe = '1h', limit = 10 }: OpportunitiesWidgetProps) {
  const router = useRouter();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['opportunities', timeframe, limit],
    queryFn: () => api.getOpportunities(timeframe, limit),
    refetchInterval: 60000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-emerald-400" />
          Top opportunités
          <span className="text-[10px] text-dim font-normal ml-auto">{timeframe}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="w-5 h-5 animate-spin text-muted" />
          </div>
        ) : isError ? (
          <p className="text-xs text-red-400">Erreur de chargement</p>
        ) : !data?.opportunities || data.opportunities.length === 0 ? (
          <p className="text-xs text-muted">Aucune opportunité pour le moment</p>
        ) : (
          <div className="space-y-1.5">
            {data.opportunities.slice(0, limit).map((opp: any, idx: number) => {
              const score = opp.combined_score ?? opp.score ?? 0;
              const scoreColor = score > 75 ? 'text-emerald-400' : score > 50 ? 'text-amber-400' : 'text-muted';
              return (
                <button
                  key={`${opp.symbol}-${idx}`}
                  onClick={() => router.push(`/lab?tab=backtest&symbol=${encodeURIComponent(opp.symbol)}&tf=${timeframe}`)}
                  className="w-full flex items-center gap-3 p-2 rounded-md hover:bg-card-hover transition-colors text-left group"
                >
                  <span className="text-[10px] text-dim font-mono w-4">{idx + 1}</span>
                  <span className="font-mono text-sm font-semibold flex-1">{opp.symbol}</span>
                  {opp.atr_pct != null && (
                    <span className="text-[10px] text-dim">
                      ATR {(opp.atr_pct * 100).toFixed(1)}%
                    </span>
                  )}
                  {opp.volume_24h != null && (
                    <span className="text-[10px] text-dim">
                      Vol ${(opp.volume_24h / 1e9).toFixed(1)}B
                    </span>
                  )}
                  <span className={cn('font-mono text-sm font-bold', scoreColor)}>
                    {score.toFixed(0)}
                  </span>
                  <ArrowRight className="w-3 h-3 text-dim opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
