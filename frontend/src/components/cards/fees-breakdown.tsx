'use client';

/**
 * S3-F1-US4 — Ventilation des frais (taker/maker/borrow/stop).
 *
 * Widget qui consomme /api/stats/fees pour afficher la répartition des frais
 * sur 30 jours. 4 segments : taker, maker, borrow (margin), stop (stop-loss).
 *
 * Aide l'utilisateur à optimiser ses coûts :
 *  - Taker élevé ? Privilégier les ordres limit (maker)
 *  - Borrow élevé ? Réduire l'usage du margin
 *  - Stop élevé ? Réviser les stops (trop serrés ?)
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { useFeesBreakdown, useRisk } from '@/hooks/use-api';
import { formatMoney, quoteCurrency } from '@/lib/utils';
import { Loader2 } from 'lucide-react';

interface FeesData {
  taker?: number;
  maker?: number;
  borrow?: number;
  stop?: number;
}

const SEGMENTS = [
  { key: 'taker', label: 'Taker', color: 'bg-red-400', desc: 'Ordres market' },
  { key: 'maker', label: 'Maker', color: 'bg-emerald-400', desc: 'Ordres limit' },
  { key: 'borrow', label: 'Borrow', color: 'bg-amber-400', desc: 'Margin' },
  { key: 'stop', label: 'Stop', color: 'bg-purple-400', desc: 'Stop-loss' },
] as const;

export function FeesBreakdown({ days = 30 }: { days?: number }) {
  const { data, isLoading, isError } = useFeesBreakdown(days);
  const { data: risk } = useRisk();
  const ccy = quoteCurrency(risk?.venues?.[0]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Ventilation des frais ({days}j)</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center py-6">
          <Loader2 className="w-5 h-5 animate-spin text-muted" />
        </CardContent>
      </Card>
    );
  }

  if (isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Ventilation des frais ({days}j)</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted">Données non disponibles</p>
        </CardContent>
      </Card>
    );
  }

  const fees = data as FeesData;
  const total = (fees.taker ?? 0) + (fees.maker ?? 0) + (fees.borrow ?? 0) + (fees.stop ?? 0);

  if (total === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Ventilation des frais ({days}j)</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted">Aucun frais sur la période</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center justify-between">
          <span>Ventilation des frais ({days}j)</span>
          <span className="font-mono text-xs text-muted">{formatMoney(total, ccy)}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Barre empilée */}
        <div className="flex h-2 rounded-full overflow-hidden bg-card-hover">
          {SEGMENTS.map((seg) => {
            const value = fees[seg.key] ?? 0;
            const pct = (value / total) * 100;
            if (pct === 0) return null;
            return (
              <div
                key={seg.key}
                className={seg.color}
                style={{ width: `${pct}%` }}
                title={`${seg.label}: ${formatMoney(value, ccy)} (${pct.toFixed(1)}%)`}
              />
            );
          })}
        </div>

        {/* Détail par segment */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          {SEGMENTS.map((seg) => {
            const value = fees[seg.key] ?? 0;
            const pct = total > 0 ? (value / total) * 100 : 0;
            return (
              <div key={seg.key} className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-sm ${seg.color}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <span className="text-muted">{seg.label}</span>
                    <span className="font-mono">{formatMoney(value, ccy)}</span>
                  </div>
                  <div className="text-[10px] text-dim">
                    {pct.toFixed(1)}% · {seg.desc}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Hint d'optimisation */}
        {fees.taker && fees.maker !== undefined && fees.taker > (fees.maker * 3) && (
          <p className="text-[10px] text-amber-400 italic pt-2 border-t border-border">
            💡 Frais taker élevés — privilégier les ordres limit pour réduire les coûts.
          </p>
        )}
        {fees.borrow && fees.borrow > total * 0.3 && (
          <p className="text-[10px] text-amber-400 italic">
            💡 Frais d&apos;emprunt margin élevés — réduire l&apos;usage du margin.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
