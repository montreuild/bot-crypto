'use client';

import { useBots, useForceBotActive, useRunForwardTest } from '@/hooks/use-api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, lifecycleStyle, parseSlotKey, formatUSD } from '@/lib/utils';
import { toast } from 'sonner';
import { Bot as BotIcon, RefreshCw, Zap, Star, AlertCircle } from 'lucide-react';
import { useState } from 'react';
import { QueryBoundary } from '@/components/ui/query-state';

export default function BotsPage() {
  const query = useBots();
  const { data } = query;
  const forceActive = useForceBotActive();
  const runForward = useRunForwardTest();
  const [filter, setFilter] = useState<string>('all');

  // S6-12 : le titre reste monté même sans données, et l'échec est réessayable.
  const header = (
    <div>
      <h1 className="text-2xl font-bold tracking-tight">Mes Bots</h1>
      <p className="text-sm text-muted mt-1">
        Portefeuille de stratégies avec cycle de vie automatique
      </p>
    </div>
  );

  if (!data) {
    return (
      <QueryBoundary
        title={header}
        query={query}
        loadingLabel="Chargement des bots…"
        onRetry={() => query.refetch()}
      >
        {null}
      </QueryBoundary>
    );
  }

  const bots = data.bots || [];
  const counts = data.counts || {};
  const filtered = filter === 'all' ? bots : bots.filter((b) => b.state === filter);

  const handleForce = async (slotKey: string, enabled: boolean) => {
    try {
      await forceActive.mutateAsync({ slotKey, enabled });
      toast.success(enabled ? 'Bot forcé en ACTIF' : 'Forçage levé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleForward = async (slotKey: string) => {
    try {
      toast.info('Forward-test en cours...');
      await runForward.mutateAsync(slotKey);
      toast.success('Forward-test terminé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  return (
    <div className="space-y-6 animate-fade-in">
      {header}

      {/* Lifecycle counts */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {(['candidat', 'essai', 'actif', 'retire'] as const).map((state) => {
          const style = lifecycleStyle(state);
          const count = counts[state] || 0;
          return (
            <button
              key={state}
              onClick={() => setFilter(filter === state ? 'all' : state)}
              className={cn(
                'p-4 rounded-xl border bg-card text-left transition-all hover:scale-[1.02]',
                filter === state ? 'border-primary-400 ring-2 ring-primary-400/20' : 'border-border',
                style.bg,
              )}
            >
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-xs uppercase tracking-wider text-dim">{style.label}</div>
                  <div className={cn('text-2xl font-bold mt-1', style.text)}>{count}</div>
                </div>
                <span className="text-2xl opacity-50">{style.icon}</span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Bots grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filtered.map((bot) => {
          const { strategy, tf, symbol } = parseSlotKey(bot.slot_key);
          const style = lifecycleStyle(bot.state);
          const budgetPct = bot.budget?.budget_pct || 0;
          const usedPct = bot.budget?.used_pct || 0;
          const edge = bot.edge;

          return (
            <Card key={bot.slot_key} className={cn('hover:border-border-hi transition-all', style.bg)}>
              <CardContent className="space-y-3">
                {/* Header */}
                <div className="flex items-start justify-between">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <BotIcon className="w-4 h-4 text-primary-400 flex-shrink-0" />
                      <span className="font-mono text-sm font-semibold truncate">{strategy}</span>
                    </div>
                    <div className="text-xs text-muted mt-0.5">
                      {tf} · {symbol || '—'}
                    </div>
                  </div>
                  <Badge variant={
                    bot.state === 'actif' ? 'success' :
                    bot.state === 'essai' ? 'info' :
                    bot.state === 'retire' ? 'danger' : 'warning'
                  }>
                    {style.icon} {style.label}
                  </Badge>
                </div>

                {/* Budget bar */}
                {bot.budget && (
                  <div>
                    <div className="flex justify-between text-[10px] text-dim mb-1">
                      <span>Budget: {budgetPct.toFixed(1)}%</span>
                      <span>Used: {usedPct.toFixed(1)}%</span>
                    </div>
                    <div className="h-1.5 bg-card-hover rounded-full overflow-hidden">
                      <div
                        className="h-full bg-primary-400 rounded-full"
                        style={{ width: `${Math.min((usedPct / Math.max(budgetPct, 1)) * 100, 100)}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* Edge stats */}
                {edge && edge.available && (
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <div className="text-dim">Edge CI Low</div>
                      <div className={cn('font-mono font-semibold', (edge.ci_low_pct || 0) > 0 ? 'text-emerald-400' : 'text-red-400')}>
                        {(edge.ci_low_pct || 0).toFixed(2)}%
                      </div>
                    </div>
                    <div>
                      <div className="text-dim">Trades</div>
                      <div className="font-mono font-semibold">{edge.n || 0}</div>
                    </div>
                    <div>
                      <div className="text-dim">Worst</div>
                      <div className="font-mono font-semibold text-red-400">
                        {(edge.worst_trade_pct || 0).toFixed(1)}%
                      </div>
                    </div>
                  </div>
                )}

                {/* Verdict */}
                {bot.verdict && (
                  <div className={cn(
                    'text-xs px-2 py-1 rounded border',
                    bot.in_band ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                                : 'bg-red-500/10 text-red-400 border-red-500/30',
                  )}>
                    {bot.in_band ? '✓' : '✗'} {bot.verdict}
                  </div>
                )}

                {/* Actions */}
                <div className="flex gap-2 pt-2 border-t border-border">
                  <Button
                    size="sm"
                    variant={bot.manual_active ? 'danger' : 'outline'}
                    onClick={() => handleForce(bot.slot_key, !bot.manual_active)}
                    disabled={forceActive.isPending}
                    className="flex-1"
                  >
                    <Star className="w-3 h-3" />
                    {bot.manual_active ? 'Unforce' : 'Force Active'}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleForward(bot.slot_key)}
                    disabled={runForward.isPending}
                  >
                    <RefreshCw className={cn('w-3 h-3', runForward.isPending && 'animate-spin')} />
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-16">
          <AlertCircle className="w-12 h-12 mx-auto text-dim mb-3" />
          <div className="text-muted">Aucun bot dans cet état</div>
        </div>
      )}
    </div>
  );
}
