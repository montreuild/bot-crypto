'use client';

/**
 * S3-F3-US1 — Bande Monte-Carlo vs réel.
 *
 * Visualisation clé de la VISION_CIBLE_BOTS_AUTONOMES.md : compare le rendement
 * réellement réalisé en live à la distribution simulée par Monte-Carlo.
 *
 * ── Note de revue d'intégration ────────────────────────────────────────────
 * La version livrée en S4 attendait des **séries temporelles** (`labels`,
 * `median`, `ci_lower`, `ci_upper`, `live` sous forme de tableaux) pour tracer
 * un cône qui s'évase dans le temps. `/api/oos-tracker` n'expose rien de tel :
 * chaque slot porte des **agrégats scalaires** —
 *
 *   monte_carlo : { runs, return_p5_pct, return_mean_pct, return_p95_pct,
 *                   max_dd_p95_pct, prob_profit }
 *   live        : { n_trades, avg_return_pct }
 *   contract    : { available, live_mean_pct, in_band, verdict }
 *
 * — si bien que `chartData` était systématiquement vide et que le composant
 * affichait « Pas encore de données OOS pour ce bot » pour **tous** les bots,
 * quelles que soient les données. Le composant rend désormais ce que l'API
 * fournit réellement : une bande [P5, P95] avec le repère simulé (moyenne) et
 * le repère live, positionnés sur un axe de rendement commun.
 *
 * Reconstituer le cône temporel d'origine demanderait un nouvel endpoint
 * exposant la trajectoire d'équité par run — à arbitrer côté backend.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { positionPct } from '@/components/charts/monte-carlo-band';

interface OosMonteCarlo {
  runs?: number;
  return_p5_pct?: number;
  return_mean_pct?: number;
  return_p95_pct?: number;
  max_dd_p95_pct?: number;
  prob_profit?: number;
}

interface OosSlot {
  slot_key?: string;
  monte_carlo?: OosMonteCarlo;
  live?: { n_trades?: number; avg_return_pct?: number | null };
  contract?: {
    available?: boolean;
    live_mean_pct?: number | null;
    in_band?: boolean | null;
    verdict?: string;
  };
  edge?: { expectancy_pct?: number; n?: number };
}

interface MonteCarloConeProps {
  slotKey: string;
  oosData: OosSlot | undefined;
  className?: string;
}

// P0-2 : positionPct importé depuis @/components/charts/monte-carlo-band

export function MonteCarloCone({ slotKey, oosData, className }: MonteCarloConeProps) {
  const mc = oosData?.monte_carlo;
  const hasBand =
    mc != null &&
    Number.isFinite(mc.return_p5_pct) &&
    Number.isFinite(mc.return_p95_pct);

  if (!hasBand) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-sm">Bande Monte-Carlo</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-32 flex items-center justify-center text-sm text-muted">
            Pas encore de simulation Monte-Carlo pour ce bot
          </div>
        </CardContent>
      </Card>
    );
  }

  const p5 = mc!.return_p5_pct as number;
  const p95 = mc!.return_p95_pct as number;
  const mean = Number.isFinite(mc!.return_mean_pct) ? (mc!.return_mean_pct as number) : (p5 + p95) / 2;

  const contract = oosData?.contract;
  const liveMean = contract?.live_mean_pct ?? oosData?.live?.avg_return_pct ?? null;
  const hasLive = liveMean != null && Number.isFinite(liveMean);
  const liveTrades = oosData?.live?.n_trades ?? 0;

  // Échelle : la bande, élargie du live et de zéro, plus 10 % de marge.
  const values = [p5, p95, 0, ...(hasLive ? [liveMean as number] : [])];
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const pad = Math.max((rawMax - rawMin) * 0.1, 0.1);
  const axisMin = rawMin - pad;
  const axisMax = rawMax + pad;

  const inBand = contract?.in_band;
  const verdict: 'ok' | 'bad' | 'na' = !contract?.available || inBand == null
    ? 'na'
    : inBand
      ? 'ok'
      : 'bad';

  const verdictConfig = {
    ok: { label: '✓ Conforme à la simulation', color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
    bad: { label: '⚠ Hors de la bande simulée', color: 'text-red-400', bg: 'bg-red-500/10' },
    na: { label: 'Pas assez de trades réels', color: 'text-amber-400', bg: 'bg-amber-500/10' },
  } as const;
  const vc = verdictConfig[verdict];

  const liveColor =
    verdict === 'ok' ? 'bg-emerald-400' : verdict === 'bad' ? 'bg-red-400' : 'bg-amber-400';

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">Monte-Carlo vs réel</CardTitle>
        <span className={cn('text-[10px] px-2 py-0.5 rounded font-medium', vc.bg, vc.color)}>
          {vc.label}
        </span>
      </CardHeader>
      <CardContent>
        {/* Bande [P5, P95] + repères sim et live */}
        <div className="pt-6 pb-2">
          <div className="relative h-8" role="img"
               aria-label={
                 `Rendement simulé entre ${p5.toFixed(2)}% et ${p95.toFixed(2)}%, moyenne ` +
                 `${mean.toFixed(2)}%` +
                 (hasLive ? `, réalisé en live ${(liveMean as number).toFixed(2)}%` : ', aucun trade live')
               }>
            {/* Rail */}
            <div className="absolute inset-x-0 top-3 h-2 rounded-full bg-card-hover" />

            {/* Bande IC 90 % (P5 → P95) */}
            <div
              className="absolute top-3 h-2 rounded-full bg-cyan-500/40"
              style={{
                left: `${positionPct(p5, axisMin, axisMax)}%`,
                width: `${positionPct(p95, axisMin, axisMax) - positionPct(p5, axisMin, axisMax)}%`,
              }}
            />

            {/* Zéro */}
            <div
              className="absolute top-1 h-6 w-px bg-border"
              style={{ left: `${positionPct(0, axisMin, axisMax)}%` }}
            />

            {/* Moyenne simulée */}
            <div
              className="absolute top-1 h-6 w-0.5 bg-cyan-400"
              style={{ left: `${positionPct(mean, axisMin, axisMax)}%` }}
              title={`Simulation (moyenne) : ${mean.toFixed(2)}%`}
            />

            {/* Réalisé live */}
            {hasLive && (
              <div
                className={cn('absolute top-0 h-8 w-1 rounded-full', liveColor)}
                style={{ left: `${positionPct(liveMean as number, axisMin, axisMax)}%` }}
                title={`Live : ${(liveMean as number).toFixed(2)}%`}
              />
            )}
          </div>

          <div className="mt-1 flex justify-between text-[10px] text-dim font-mono">
            <span>{p5.toFixed(2)}%</span>
            <span>{p95.toFixed(2)}%</span>
          </div>
        </div>

        {/* Légende chiffrée */}
        <div className="grid grid-cols-3 gap-2 text-[10px]">
          <div>
            <div className="text-dim">SIM (MOY.)</div>
            <div className="font-mono text-cyan-400">{mean.toFixed(2)}%</div>
          </div>
          <div>
            <div className="text-dim">LIVE</div>
            <div className="font-mono">
              {hasLive ? `${(liveMean as number).toFixed(2)}%` : '—'}
            </div>
          </div>
          <div>
            <div className="text-dim">PROB. GAIN</div>
            <div className="font-mono">
              {Number.isFinite(mc!.prob_profit) ? `${mc!.prob_profit}%` : '—'}
            </div>
          </div>
        </div>

        <div className="mt-2 flex items-center justify-between text-[10px] text-dim">
          <span>{mc!.runs ?? 0} runs · {liveTrades} trade{liveTrades > 1 ? 's' : ''} live</span>
          {Number.isFinite(mc!.max_dd_p95_pct) && (
            <span className="font-mono">DD P95 : {mc!.max_dd_p95_pct}%</span>
          )}
        </div>

        <p className="mt-2 text-[10px] text-muted leading-relaxed">
          La bande couvre les rendements simulés entre le 5ᵉ et le 95ᵉ centile sur{' '}
          {mc!.runs ?? 0} tirages. Le rendement réalisé en live doit rester dans la
          bande pour confirmer la robustesse hors-échantillon.
        </p>
      </CardContent>
    </Card>
  );
}
