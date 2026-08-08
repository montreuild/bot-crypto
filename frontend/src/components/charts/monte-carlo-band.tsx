/**
 * F4 — MonteCarloBand — bande [P5, P95] réutilisable.
 *
 * Factorisation partielle : `monte-carlo-cone.tsx` (cône OOS live sur /bots)
 * et `monte-carlo-panel.tsx` (chart backtest sur /lab) partagent le concept
 * visuel d'une bande P5/P95 avec un repère moyenne et un repère live/capital,
 * mais leurs sources de données sont différentes (scalaires agrégés vs arrays
 * temporels). Ce module fournit :
 *
 *   1. `positionPct(value, min, max)` : utilitaire de positionnement sur une
 *      bande, borné à [0, 100]. Était dupliqué dans les 2 composants.
 *   2. `<MonteCarloBand>` : composant de rendu visuel d'une bande avec repères
 *      moyenne + live, utilisable dans les deux contextes (props scalaires).
 *
 * Migration progressive : les composants existants peuvent importer
 * `positionPct` et `MonteCarloBand` sans casser leur contrat actuel.
 */

import type { ReactNode } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

/** Position en % sur une bande, bornée à [0, 100]. */
export function positionPct(value: number, min: number, max: number): number {
  if (!Number.isFinite(value) || max <= min) return 50;
  return Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100));
}

export interface MonteCarloBandProps {
  /** Titre de la carte. */
  title?: string;
  /** Borne inférieure de la bande (P5). */
  p5: number;
  /** Borne supérieure de la bande (P95). */
  p95: number;
  /** Repère moyenne (centre de la bande). */
  mean?: number | null;
  /** Repère live / capital réel. */
  live?: number | null;
  /** Label pour le repère live (ex: "Live", "Capital"). */
  liveLabel?: string;
  /** Unité affichée (ex: "%", "$"). */
  unit?: string;
  /** True si le live est dans la bande [P5, P95]. */
  inBand?: boolean | null;
  /** Verdict optionnel (ex: "OK", "Hors bande"). */
  verdict?: string | null;
  /** Children additionnels (stats, explications). */
  children?: ReactNode;
  /** Classes Tailwind additionnelles. */
  className?: string;
  /** Message si données indisponibles. */
  emptyLabel?: string;
}

export function MonteCarloBand({
  title = 'Bande Monte-Carlo',
  p5,
  p95,
  mean,
  live,
  liveLabel = 'Live',
  unit = '%',
  inBand,
  verdict,
  children,
  className,
  emptyLabel,
}: MonteCarloBandProps) {
  const hasBand = Number.isFinite(p5) && Number.isFinite(p95);
  if (!hasBand) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-sm">{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center text-muted text-xs py-6 italic">
            {emptyLabel ?? 'Pas encore de données Monte-Carlo'}
          </div>
        </CardContent>
      </Card>
    );
  }

  // Étendre légèrement la plage pour que les repères aux bornes restent visibles
  const padding = (p95 - p5) * 0.1;
  const min = p5 - padding;
  const max = p95 + padding;

  const meanPos = mean != null && Number.isFinite(mean) ? positionPct(mean, min, max) : null;
  const livePos = live != null && Number.isFinite(live) ? positionPct(live, min, max) : null;
  const p5Pos = positionPct(p5, min, max);
  const p95Pos = positionPct(p95, min, max);

  // Couleur du live selon inBand
  const liveColor = inBand == null
    ? 'bg-cyan-500'
    : inBand
      ? 'bg-emerald-500'
      : 'bg-red-500';

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Bande visuelle */}
        <div className="relative h-12 bg-card-hover/50 rounded-md overflow-hidden">
          {/* Bande [P5, P95] */}
          <div
            className="absolute top-1 bottom-1 bg-cyan-500/20 border-x border-cyan-500/40"
            style={{ left: `${p5Pos}%`, width: `${p95Pos - p5Pos}%` }}
          />
          {/* Repère moyenne */}
          {meanPos != null && (
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-cyan-400"
              style={{ left: `${meanPos}%` }}
              title={`Moyenne: ${mean?.toFixed(2)}${unit}`}
            />
          )}
          {/* Repère live */}
          {livePos != null && (
            <div
              className={cn('absolute top-0 bottom-0 w-1', liveColor)}
              style={{ left: `calc(${livePos}% - 2px)` }}
              title={`${liveLabel}: ${live?.toFixed(2)}${unit}`}
            />
          )}
          {/* Labels P5 / P95 */}
          <span className="absolute left-1 top-1 text-[10px] text-muted-foreground font-mono">
            P5 {p5.toFixed(1)}{unit}
          </span>
          <span className="absolute right-1 top-1 text-[10px] text-muted-foreground font-mono">
            P95 {p95.toFixed(1)}{unit}
          </span>
        </div>

        {/* Légende + verdict */}
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-3">
            {mean != null && (
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 bg-cyan-400 inline-block" />
                <span className="text-muted-foreground">Moy: </span>
                <span className="font-mono">{mean.toFixed(2)}{unit}</span>
              </span>
            )}
            {live != null && (
              <span className="flex items-center gap-1">
                <span className={cn('w-2 h-2 inline-block', liveColor)} />
                <span className="text-muted-foreground">{liveLabel}: </span>
                <span className="font-mono">{live.toFixed(2)}{unit}</span>
              </span>
            )}
          </div>
          {verdict && (
            <span className={cn(
              'text-xs font-semibold',
              inBand == null ? 'text-muted-foreground' : inBand ? 'text-emerald-400' : 'text-red-400',
            )}>
              {verdict}
            </span>
          )}
        </div>

        {children}
      </CardContent>
    </Card>
  );
}
