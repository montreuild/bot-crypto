'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { useBotStatus } from '@/hooks/use-api';
import { cn, parseSlotKey } from '@/lib/utils';
import { useRouter } from 'next/navigation';

export function AllocationsGrid() {
  const { data: status } = useBotStatus();
  const allocations = status?.capital_allocation || [];
  const router = useRouter();

  if (allocations.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Allocation par Slot</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-sm text-muted">
            Aucun slot actif
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Allocation par Slot</CardTitle>
        <span className="text-xs text-dim">{allocations.length} slots</span>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {allocations.slice(0, 12).map((slot) => {
            const { strategy, tf, symbol } = parseSlotKey(slot.slot_key);
            const usedPct = slot.used_pct || 0;
            const budgetPct = slot.budget_pct || 0;
            const utilization = budgetPct > 0 ? (usedPct / budgetPct) * 100 : 0;
            const isPaused = slot.paused;
            const isExcluded = slot.excluded_by_optimizer;

            return (
              <button
                key={slot.slot_key}
                onClick={() => router.push('/bots')}
                className={cn(
                  'text-left p-3 rounded-lg border bg-card-hover transition-all hover:border-border-hi hover:scale-[1.02]',
                  isPaused && 'border-red-500/30 bg-red-500/5',
                  isExcluded && 'opacity-50 border-dashed',
                )}
              >
                <div className="font-mono text-xs font-semibold text-primary-400 truncate">
                  {strategy}
                </div>
                <div className="text-[10px] text-muted mt-0.5">
                  {tf} · {symbol || '—'}
                </div>

                <div className="mt-3">
                  <div className="flex justify-between text-[10px] text-dim mb-1">
                    <span>{usedPct.toFixed(1)}% / {budgetPct.toFixed(1)}%</span>
                    <span>${(slot.budget_usdc || 0).toFixed(0)}</span>
                  </div>
                  <div className="h-1.5 bg-card rounded-full overflow-hidden">
                    <div
                      className={cn(
                        'h-full rounded-full transition-all',
                        utilization > 80 ? 'bg-amber-400' : utilization > 50 ? 'bg-cyan-400' : 'bg-emerald-400',
                      )}
                      style={{ width: `${Math.min(utilization, 100)}%` }}
                    />
                  </div>
                </div>

                {isPaused && (
                  <div className="mt-2 text-[10px] text-red-400 font-semibold">
                    ⏸ {slot.pause_reason}
                  </div>
                )}
                {slot.consecutive_losses > 0 && (
                  <div className="mt-1 text-[10px] text-amber-400">
                    {slot.consecutive_losses} pertes consécutives
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
