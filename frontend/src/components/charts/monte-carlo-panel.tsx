'use client';

/**
 * E3-F2-US2 — Monte-Carlo du backtest.
 *
 * Contrat réel de `MonteCarlo.run()` (app/engine/monte_carlo.py) :
 *   { runs, confidence, final_equity_mean, final_equity_p5, final_equity_p95,
 *     max_dd_p95, prob_profit, prob_ruin_10pct }
 * Échec : { error: "Aucun trade fermé" }
 *
 * `/lab` lisait `monte_carlo.p5 / .p50 / .p95` — aucun de ces champs n'existe :
 * la ligne affichait « P5: $0.00 · P50: $0.00 · P95: $0.00 » quel que soit le
 * résultat.
 *
 * ⚠ L'audit spécifiait « graphique Monte-Carlo (200 runs, IC 95%, P5/P50/P95) »
 * sous forme de 3 COURBES. Ce n'est pas implémentable : le backend ne renvoie
 * que des AGRÉGATS SCALAIRES sur l'équité finale, jamais la trajectoire des
 * runs. Tracer des courbes exigerait un nouvel endpoint exposant l'équité par
 * run — c'est exactement l'erreur commise en S4 sur le cône (R17), où un
 * composant avait été écrit contre des séries temporelles inexistantes et
 * restait vide pour tous les bots. On visualise donc la bande [P5, P95] avec le
 * repère moyen et le capital initial, ce que les données permettent réellement.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { AlertTriangle, Dices } from 'lucide-react';
import { cn, formatUSD } from '@/lib/utils';
import { positionPct } from '@/components/charts/monte-carlo-band';

export interface MonteCarloData {
  error?: string;
  runs?: number;
  confidence?: number;
  final_equity_mean?: number;
  final_equity_p5?: number;
  final_equity_p95?: number;
  max_dd_p95?: number;
  prob_profit?: number;
  prob_ruin_10pct?: number;
}

export function MonteCarloPanel({
  data,
  initialCapital,
  nTrades,
}: {
  data: MonteCarloData;
  initialCapital?: number;
  nTrades?: number;
}) {
  if (data?.error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Dices className="w-3.5 h-3.5" />
            Monte-Carlo
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-start gap-2 text-xs text-amber-400 p-2 rounded bg-amber-500/5 border border-amber-500/20">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>{data.error}</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  const p5 = Number(data?.final_equity_p5 ?? 0);
  const p95 = Number(data?.final_equity_p95 ?? 0);
  const mean = Number(data?.final_equity_mean ?? 0);
  const capital = Number(initialCapital ?? 0);
  const probProfit = Number(data?.prob_profit ?? 0);
  const probRuin = Number(data?.prob_ruin_10pct ?? 0);

  if (!p5 && !p95 && !mean) return null;

  // Échelle de la bande : on inclut le capital initial pour que le repère de
  // seuil de rentabilité soit toujours visible, même si toute la distribution
  // est d'un seul côté.
  const lo = Math.min(p5, capital || p5);
  const hi = Math.max(p95, capital || p95);
  // P0-2 : `positionPct` partagé avec `monte-carlo-cone.tsx`. La copie locale
  // qui vivait ici était la moitié restante de la duplication que
  // `monte-carlo-band.tsx` avait été créé pour absorber.
  const pos = (v: number) => positionPct(v, lo, hi);

  // < 30 trades : le rééchantillonnage porte sur trop peu d'observations pour
  // que les percentiles soient interprétables (avertissement prévu par l'audit).
  const tooFewTrades = typeof nTrades === 'number' && nTrades < 30;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Dices className="w-3.5 h-3.5" />
          Monte-Carlo — {data.runs ?? 0} runs
        </CardTitle>
        <Badge variant={probProfit >= 50 ? 'success' : 'danger'}>
          {probProfit.toFixed(1)}% de runs profitables
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {tooFewTrades && (
          <div className="flex items-start gap-2 text-xs text-amber-400 p-2 rounded bg-amber-500/5 border border-amber-500/20">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              Seulement {nTrades} trades — en dessous de 30, les percentiles
              Monte-Carlo ne sont pas statistiquement exploitables.
            </span>
          </div>
        )}

        {/* Bande [P5, P95] sur l'équité finale */}
        <div>
          <div className="flex justify-between text-[10px] text-dim mb-1">
            <span>Équité finale — intervalle {(Number(data.confidence ?? 0.95) * 100).toFixed(0)}%</span>
          </div>
          <div className="relative h-8">
            {/* Rail */}
            <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-2 bg-card-hover rounded-full" />
            {/* Bande P5 → P95 */}
            <div
              className="absolute top-1/2 -translate-y-1/2 h-2 bg-cyan-500/30 border-y border-cyan-500/50"
              style={{ left: `${pos(p5)}%`, width: `${Math.max(pos(p95) - pos(p5), 0.5)}%` }}
            />
            {/* Repère capital initial (seuil de rentabilité) */}
            {capital > 0 && (
              <div
                className="absolute top-0 bottom-0 w-px bg-muted"
                style={{ left: `${pos(capital)}%` }}
                title={`Capital initial : ${formatUSD(capital)}`}
              />
            )}
            {/* Repère moyenne */}
            <div
              className="absolute top-1/2 -translate-y-1/2 w-1 h-4 rounded-sm bg-cyan-300"
              style={{ left: `${pos(mean)}%` }}
              title={`Moyenne : ${formatUSD(mean)}`}
            />
          </div>
          <div className="flex justify-between text-[10px] font-mono mt-1">
            <span className={cn(p5 >= capital ? 'text-emerald-400' : 'text-red-400')}>
              P5 {formatUSD(p5)}
            </span>
            <span className="text-muted">moy. {formatUSD(mean)}</span>
            <span className="text-emerald-400">P95 {formatUSD(p95)}</span>
          </div>
        </div>

        {/* Probabilités */}
        <div className="grid grid-cols-3 gap-2 text-xs pt-2 border-t border-border">
          <div>
            <div className="text-dim">Prob. de gain</div>
            <div className={cn('font-mono font-semibold', probProfit >= 50 ? 'text-emerald-400' : 'text-red-400')}>
              {probProfit.toFixed(1)}%
            </div>
          </div>
          <div>
            <div className="text-dim">Prob. de perte &gt; 10%</div>
            <div className={cn('font-mono font-semibold', probRuin <= 10 ? 'text-emerald-400' : probRuin <= 30 ? 'text-amber-400' : 'text-red-400')}>
              {probRuin.toFixed(1)}%
            </div>
          </div>
          <div>
            <div className="text-dim">Max DD (P95)</div>
            <div className="font-mono font-semibold text-red-400">
              {Number(data.max_dd_p95 ?? 0).toFixed(2)}%
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
