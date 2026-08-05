'use client';

/**
 * Top opportunités — Vol/ATR (cache) + signaux SMC récents (< 5 j).
 * Clic → Smart Graph (symbole + TF).
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Loader2, ArrowRight, TrendingUp, RefreshCw, Target } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { useState } from 'react';

interface OpportunitiesWidgetProps {
  timeframe?: string;
  limit?: number;
}

function formatSignalTime(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  const d = new Date(n < 1e11 ? n * 1000 : n);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function fmtVol(v: number): string {
  if (!Number.isFinite(v) || v <= 0) return '—';
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}k`;
  return String(Math.round(v));
}

export function OpportunitiesWidget({ timeframe: tfProp, limit = 12 }: OpportunitiesWidgetProps) {
  const router = useRouter();
  const { defaultTf } = useTradingTimeframes(tfProp || '1h');
  const [tf, setTf] = useState(tfProp || defaultTf);

  const { data, isLoading, isError, isFetching, refetch, error } = useQuery({
    queryKey: ['opportunities', tf, limit],
    queryFn: () => api.getOpportunities(tf, limit),
    refetchInterval: 120_000,
    staleTime: 60_000,
    retry: 1,
  });

  const smcQ = useQuery({
    queryKey: ['smcSignalsRecent'],
    queryFn: () => api.getSmcSignalsRecent(),
    refetchInterval: 180_000,
    staleTime: 90_000,
    retry: 1,
  });

  const opps = data?.opportunities || [];
  const smcSignals = smcQ.data?.signals || [];

  const goSmartGraph = (symbol: string, timeframe: string) => {
    router.push(
      `/market?tab=smartgraph&symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(timeframe)}`,
    );
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
            <TrendingUp className="w-4 h-4 text-emerald-400" />
            Top opportunités
            <Badge variant="muted" className="text-[10px] font-normal">Vol + ATR%</Badge>
            <div className="ml-auto flex items-center gap-2">
              <TimeframeButtons value={tf} onChange={setTf} size="sm" />
              <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching} aria-label="Rafraîchir">
                <RefreshCw className={cn('w-3.5 h-3.5', isFetching && 'animate-spin')} />
              </Button>
            </div>
          </CardTitle>
          <p className="text-[11px] text-muted font-normal">
            Score = 40&nbsp;% volume 24h + 60&nbsp;% ATR% (volatilité) — cache OHLCV, pas un signal d&apos;entrée.
            Clic → Smart Graph.
          </p>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-6 gap-2 text-xs text-muted">
              <Loader2 className="w-5 h-5 animate-spin" />
              Analyse cache OHLCV…
            </div>
          ) : isError ? (
            <p className="text-xs text-red-400">
              Erreur : {(error as Error)?.message || 'chargement impossible'}
            </p>
          ) : opps.length === 0 ? (
            <div className="text-xs text-muted space-y-1">
              <p>Aucune opportunité Vol/ATR pour {tf} (filtres ou cache vide).</p>
              <p className="text-dim">
                Astuce : peupler le cache (Données → backfill) ou « Scanner le marché ».
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
              {opps.slice(0, limit).map((opp: any, idx: number) => {
                const score = Number(opp.combined_score ?? opp.score ?? 0);
                const scoreColor = score > 75 ? 'text-emerald-400' : score > 50 ? 'text-amber-400' : 'text-muted';
                const atr = opp.atr_pct ?? opp.indicators?.atr_pct;
                const vol = Number(opp.volume_24h ?? 0);
                const sigT = opp.signal_time ?? opp.as_of;
                return (
                  <button
                    key={`${opp.symbol}-${idx}`}
                    type="button"
                    onClick={() => goSmartGraph(opp.symbol, tf)}
                    className="w-full flex flex-col gap-1 p-2 rounded-md hover:bg-card-hover border border-border/50 transition-colors text-left group"
                  >
                    <div className="flex items-center gap-2 w-full">
                      <span className="text-[10px] text-dim font-mono w-4">{idx + 1}</span>
                      <span className="font-mono text-sm font-semibold truncate flex-1">{opp.symbol}</span>
                      <span className="text-[10px] text-dim font-mono whitespace-nowrap">
                        {formatSignalTime(sigT)}
                      </span>
                      <span className={cn('font-mono text-sm font-bold', scoreColor)}>
                        {score.toFixed(0)}
                      </span>
                      <ArrowRight className="w-3 h-3 text-dim opacity-0 group-hover:opacity-100 shrink-0" />
                    </div>
                    <div className="flex items-center gap-2 pl-6 text-[10px] text-dim">
                      <Badge variant="muted" className="text-[9px] px-1 py-0">Vol+ATR</Badge>
                      <span>Vol {fmtVol(vol)}</span>
                      {atr != null && <span>ATR {Number(atr).toFixed(2)}%</span>}
                      {opp.regime_label && <span>{opp.regime_label}</span>}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Signaux SMC récents (< 5 jours) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
            <Target className="w-4 h-4 text-cyan-400" />
            Signaux SMC récents
            <Badge variant="info" className="text-[10px] font-normal">&lt; 5 j · smart_money</Badge>
            <div className="ml-auto">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => smcQ.refetch()}
                disabled={smcQ.isFetching}
                aria-label="Rafraîchir SMC"
              >
                <RefreshCw className={cn('w-3.5 h-3.5', smcQ.isFetching && 'animate-spin')} />
              </Button>
            </div>
          </CardTitle>
          <p className="text-[11px] text-muted font-normal">
            Plans SMC (1h / 4h / 1d) détectés sur le cache — job background.
            {smcQ.data?.updated_at && (
              <span className="text-dim"> · Maj {formatSignalTime(
                smcQ.data.updated_ts ?? Date.parse(smcQ.data.updated_at) / 1000,
              )}</span>
            )}
          </p>
        </CardHeader>
        <CardContent>
          {smcQ.isLoading ? (
            <div className="flex items-center justify-center py-4 gap-2 text-xs text-muted">
              <Loader2 className="w-4 h-4 animate-spin" />
              Chargement signaux SMC…
            </div>
          ) : smcSignals.length === 0 ? (
            <p className="text-xs text-muted">
              Aucun signal SMC récent (scan en cours ou cache insuffisant &lt; 260 barres).
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
              {smcSignals.slice(0, 18).map((s: any, idx: number) => {
                const isLong = s.side === 'long';
                return (
                  <button
                    key={`${s.symbol}-${s.timeframe}-${s.setup}-${idx}`}
                    type="button"
                    onClick={() => goSmartGraph(s.symbol, s.timeframe || '1h')}
                    className="w-full flex flex-col gap-1 p-2 rounded-md hover:bg-card-hover border border-border/50 transition-colors text-left group"
                  >
                    <div className="flex items-center gap-2 w-full">
                      <span className="font-mono text-sm font-semibold truncate flex-1">{s.symbol}</span>
                      <Badge variant="muted" className="text-[9px] font-mono">{s.timeframe}</Badge>
                      <span className={cn('text-[10px] font-semibold', isLong ? 'text-emerald-400' : 'text-red-400')}>
                        {String(s.side || '—').toUpperCase()}
                      </span>
                      <ArrowRight className="w-3 h-3 text-dim opacity-0 group-hover:opacity-100" />
                    </div>
                    <div className="flex items-center gap-2 text-[10px] text-dim flex-wrap">
                      <span className="text-cyan-400 font-mono">{s.setup || 'SMC'}</span>
                      <span>{formatSignalTime(s.signal_time)}</span>
                      {s.score_min != null && <span>sc {Number(s.score_min).toFixed(2)}</span>}
                      {s.rr != null && <span>RR {Number(s.rr).toFixed(1)}</span>}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
