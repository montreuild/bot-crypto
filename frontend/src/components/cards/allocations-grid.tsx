'use client';

/**
 * Grille des slots — S12 : enveloppe et risque engagé, jamais l'un sans l'autre.
 *
 * La barre mesure le **risque engagé sur le risque max du slot**, pas un
 * notionnel sur un budget : c'est la perte encourue qui décide si le prochain
 * trade passera. Le poids affiché à côté est la part du slot dans l'enveloppe
 * de son symbole, pondérée par confiance d'edge.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { useRisk } from '@/hooks/use-api';
import { cn, parseSlotKey } from '@/lib/utils';
import { useRouter } from 'next/navigation';

export function AllocationsGrid() {
  const { data } = useRisk();
  const slots = data?.slots ?? [];
  const router = useRouter();

  if (slots.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Enveloppes par slot</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-sm text-muted">Aucun slot actif</div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Enveloppes par slot</CardTitle>
        <span className="text-xs text-dim">{slots.length} slots</span>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {slots.slice(0, 12).map((slot) => {
            const { strategy, tf } = parseSlotKey(slot.slot_key);
            const riskMax = slot.risk_amount ?? 0;
            const used = riskMax > 0 ? (slot.risk_engaged ?? 0) / riskMax : 0;
            // Poids nul = edge non prouvée positive : le slot existe mais ne
            // tradera pas tant qu'elle ne l'est pas (§2.3).
            const unproven = (slot.weight ?? 0) <= 0;

            return (
              <button
                key={slot.slot_key}
                onClick={() => router.push(`/bots?slot=${encodeURIComponent(slot.slot_key)}`)}
                className={cn(
                  'text-left p-3 rounded-lg border bg-card-hover transition-all hover:border-border-hi hover:scale-[1.02]',
                  unproven && 'opacity-50 border-dashed',
                )}
              >
                <div className="font-mono text-xs font-semibold text-primary-400 truncate">
                  {strategy}
                </div>
                <div className="text-[10px] text-muted mt-0.5">
                  {tf} · {slot.symbol || '—'}
                </div>

                <div className="mt-3">
                  <div className="flex justify-between text-[10px] text-dim mb-1 tabular-nums">
                    <span>
                      {(slot.risk_engaged ?? 0).toFixed(2)} / {riskMax.toFixed(2)} {slot.currency}
                    </span>
                    <span>{((slot.weight ?? 0) * 100).toFixed(0)}%</span>
                  </div>
                  <div className="h-1.5 bg-card rounded-full overflow-hidden">
                    <div
                      className={cn(
                        'h-full rounded-full transition-all',
                        used > 0.8 ? 'bg-amber-400' : used > 0.5 ? 'bg-cyan-400' : 'bg-emerald-400',
                      )}
                      style={{ width: `${Math.min(used * 100, 100)}%` }}
                    />
                  </div>
                  <div className="text-[10px] text-muted mt-1 tabular-nums">
                    enveloppe {(slot.envelope ?? 0).toFixed(0)} {slot.currency}
                  </div>
                </div>

                {unproven && (
                  <div className="mt-2 text-[10px] text-dim">edge non prouvée</div>
                )}
              </button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
