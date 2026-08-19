'use client';

/**
 * S12 §5.1 — « Étude vs Réel » : les deux passes du backtest côte à côte.
 *
 * Deux questions différentes, pas deux mesures de la même chose :
 *   - `reference` (échelle fixe) : « cette stratégie vaut-elle quelque chose ? »
 *   - `live` (enveloppe réelle du slot) : « ce bot est-il promouvable ? »
 *
 * Le sizing étant linéaire en l'enveloppe, le PnL en % est invariant à
 * l'échelle **tant qu'aucune contrainte absolue ne mord**. Un écart entre les
 * deux passes ne peut donc venir que du notionnel minimum, de la
 * quantification, des frais fixes ou d'une saturation de budget — et les
 * compteurs de refus disent lequel. C'est ce qui rend l'écart *explicable*
 * plutôt que simplement constaté : il chiffre le coût de la gestion du risque.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

interface PassSummary {
  initial_capital: number;
  total_trades: number;
  total_pnl: number;
  pnl_pct: number;
  max_drawdown: number;
  rejections?: { total?: number; par_motif?: Record<string, number> };
}

interface Props {
  runs?: {
    reference?: PassSummary;
    live?: PassSummary;
    ecart_pnl_pct?: number;
  } | null;
  envelope?: {
    venue?: string; symbol?: string; currency?: string;
    slot_envelope?: number; weight?: number;
    trade_risk_pct?: number; risk_amount?: number; min_notional?: number;
  } | null;
}

function fmt(n: number | null | undefined, digits: number): string {
  if (n == null || Number.isNaN(Number(n))) return '—';
  return Number(n).toFixed(digits);
}

/** Un écart nul (au bruit d'arrondi près) signifie qu'aucune contrainte
 *  absolue ne mord : l'échelle réelle se comporte comme l'étude. */
const NEGLIGIBLE = 0.01;

function Column({ title, subtitle, s, currency }: {
  title: string; subtitle: string; s?: PassSummary; currency: string;
}) {
  if (!s) return null;
  return (
    <div className="space-y-1.5">
      <div>
        <div className="text-xs font-semibold">{title}</div>
        <div className="text-[10px] text-muted">{subtitle}</div>
      </div>
      <div className="text-[11px] space-y-0.5 tabular-nums">
        <div className="flex justify-between">
          <span className="text-dim">base</span>
          <span className="font-mono">{s.initial_capital.toFixed(2)} {currency}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-dim">PnL</span>
          <span className={cn('font-mono', s.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400')}>
            {s.pnl_pct >= 0 ? '+' : ''}{s.pnl_pct.toFixed(2)}%
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-dim">trades</span>
          <span className="font-mono">{s.total_trades}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-dim">drawdown</span>
          <span className="font-mono">{s.max_drawdown.toFixed(2)}%</span>
        </div>
      </div>
    </div>
  );
}

export function StudyVsLiveCard({ runs, envelope }: Props) {
  if (!runs?.live || !runs?.reference) return null;

  const currency = envelope?.currency ?? '';
  const gap = runs.ecart_pnl_pct ?? (runs.live.pnl_pct - runs.reference.pnl_pct);
  const explained = Math.abs(gap) > NEGLIGIBLE;

  // Les motifs qui expliquent l'écart : ceux de la passe LIVE que la passe
  // d'étude n'a pas rencontrés (ou beaucoup moins).
  const liveMotifs = runs.live.rejections?.par_motif ?? {};
  const refMotifs = runs.reference.rejections?.par_motif ?? {};
  const causes = Object.entries(liveMotifs)
    .map(([code, n]) => ({ code, live: n, reference: refMotifs[code] ?? 0 }))
    .filter((c) => c.live > c.reference)
    .sort((a, b) => b.live - a.live);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Étude vs Réel</CardTitle>
        <span
          className={cn('text-xs tabular-nums', explained ? 'text-amber-400' : 'text-emerald-400')}
          title="Écart de PnL % entre l'enveloppe réelle et l'échelle d'étude"
        >
          écart {gap >= 0 ? '+' : ''}{gap.toFixed(2)} pt
        </span>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <Column
            title="Étude" subtitle="échelle fixe, comparable entre bots"
            s={runs.reference} currency={currency}
          />
          <Column
            title="Réel" subtitle="enveloppe du slot — pilote la promotion"
            s={runs.live} currency={currency}
          />
        </div>

        {envelope && (
          <div className="text-[10px] text-muted border-t border-border pt-2 tabular-nums">
            {envelope.venue ?? '—'} · {envelope.symbol ?? '—'} — enveloppe{' '}
            {fmt(envelope.slot_envelope, 2)} {envelope.currency ?? ''} (poids{' '}
            {fmt((envelope.weight ?? 0) * 100, 1)}%), risque par trade{' '}
            {fmt(
              envelope.risk_amount
                ?? ((envelope.slot_envelope ?? 0) * (envelope.trade_risk_pct ?? 0)),
              2,
            )}{' '}
            {envelope.currency ?? ''}
            {(envelope.min_notional ?? 0) > 0 &&
              ` · notionnel minimum ${fmt(envelope.min_notional, 0)} ${envelope.currency ?? ''}`}
          </div>
        )}

        {/*
          Activer cette option change AUSSI les chiffres du haut de page : ils
          deviennent ceux de la passe « Réel », dimensionnée sur l'enveloppe
          fixe du slot, là où le mode par défaut capitalise sur le capital de
          la venue. L'écart est réel et voulu — mais il ne doit pas surprendre.
        */}
        <p className="text-[10px] text-dim">
          Option active : les métriques de ce backtest sont celles de la passe
          « Réel » (enveloppe du slot). Sans l&apos;option, elles sont calculées
          sur le capital de la venue, avec capitalisation.
        </p>

        {!explained ? (
          <p className="text-[11px] text-emerald-400/90">
            Aucun écart : à cette échelle, aucune contrainte absolue ne mord. Le
            comportement réel suit l&apos;étude.
          </p>
        ) : causes.length > 0 ? (
          <div>
            <p className="text-[11px] text-muted mb-1.5">
              L&apos;écart vient de contraintes qui ne se mettent pas à l&apos;échelle :
            </p>
            <ul className="space-y-1">
              {causes.map((c) => (
                <li key={c.code} className="flex justify-between text-[11px]">
                  <span className="font-mono text-amber-400">{c.code}</span>
                  <span className="text-dim tabular-nums">
                    {c.live} refus en réel · {c.reference} à l&apos;étude
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-[11px] text-muted">
            Écart sans motif de refus associé : il vient de l&apos;arrondi des
            tailles ou des frais fixes, pas d&apos;un trade bloqué.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
