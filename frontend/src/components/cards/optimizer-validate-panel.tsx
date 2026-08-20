'use client';

/**
 * P1-4 — Validation d'un paramétrage optimisé (Monte-Carlo / régimes).
 *
 * `POST /api/optimize/validate` rejoue les `best_params` d'un job terminé et
 * répond à deux questions distinctes :
 *
 * - **Monte-Carlo** : en rebattant l'ordre des trades, quelle dispersion de
 *   résultat, et quelle probabilité de ruine ? Un PnL flatteur obtenu grâce à
 *   trois trades chanceux se voit ici.
 * - **Régimes** : ce paramétrage tient-il en marché haussier, baissier, en
 *   range ? Une stratégie qui ne gagne qu'en bull n'est pas une stratégie,
 *   c'est un pari directionnel.
 *
 * La validation est déclenchée à la demande : chaque appel relance un backtest
 * complet côté serveur (limité à 5/min).
 */

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { FlaskConical, Loader2, Dices, LineChart } from 'lucide-react';
import { toast } from 'sonner';
import {cn, formatUSD, errorMessage} from '@/lib/utils';
import { useOptimizeValidate } from '@/hooks/use-api';
import type { OptimizeValidateResult } from '@/types';
import { MonteCarloPanel, type MonteCarloData } from '@/components/charts/monte-carlo-panel';
import { formatPctPoints } from '@/lib/backend-normalizers';

type Methode = 'monte_carlo' | 'regime';

/** Couleur d'un PnL — vert au-dessus de zéro, rouge en dessous. */
function tonPnl(v: number) {
  return v > 0 ? 'text-emerald-400' : v < 0 ? 'text-rose-400' : 'text-muted';
}

const LIBELLE_REGIME: Record<string, string> = {
  bull: 'Haussier',
  bear: 'Baissier',
  range: 'Range',
  high_vol: 'Volatilité forte',
  low_vol: 'Volatilité faible',
  unassigned: 'Hors régime identifié',
};

function ResultatRegimes({ data }: { data: OptimizeValidateResult }) {
  const parStrategie = data?.by_strategy ?? {};
  const marche = data?.market ?? {};
  const regimes = Object.keys(parStrategie);

  if (regimes.length === 0) {
    return (
      <p className="text-xs text-muted">
        Aucun trade n&apos;a pu être rattaché à un régime : la période est trop courte pour que
        des segments soient identifiés, ou le paramétrage n&apos;a produit aucun trade.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-muted">
              <th scope="col" className="py-1 px-2 text-left">Régime</th>
              <th scope="col" className="py-1 px-2 text-right">Trades</th>
              <th scope="col" className="py-1 px-2 text-right">PnL</th>
              <th scope="col" className="py-1 px-2 text-right">Gain %</th>
              <th scope="col" className="py-1 px-2 text-right">Win rate</th>
              <th scope="col" className="py-1 px-2 text-right">PnL moyen</th>
              <th scope="col" className="py-1 px-2 text-right">Marché</th>
            </tr>
          </thead>
          <tbody>
            {regimes.map((r) => {
              const s = parStrategie[r];
              const m = marche[r];
              return (
                <tr key={r} className="border-b border-border/50">
                  <td className="py-1.5 px-2">
                    {LIBELLE_REGIME[r] ?? r}
                    {r === 'unassigned' && (
                      <span className="block text-[10px] text-dim">
                        segments trop courts pour être classés
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 px-2 text-right font-mono">{s.n_trades}</td>
                  <td className={cn('py-1.5 px-2 text-right font-mono font-semibold', tonPnl(s.pnl))}>
                    {formatUSD(s.pnl, { sign: true })}
                  </td>
                  <td className={cn('py-1.5 px-2 text-right font-mono', tonPnl(s.pnl_pct ?? 0))}>
                    {formatPctPoints(s.pnl_pct)}
                  </td>
                  <td className="py-1.5 px-2 text-right font-mono">{formatPctPoints(s.win_rate)}</td>
                  <td className={cn('py-1.5 px-2 text-right font-mono', tonPnl(s.avg_pnl))}>
                    {formatUSD(s.avg_pnl, { sign: true })}
                  </td>
                  <td className="py-1.5 px-2 text-right font-mono text-dim">
                    {m ? `${m.avg_return_pct > 0 ? '+' : ''}${m.avg_return_pct} %` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-dim">
        La colonne « Marché » donne le rendement moyen du sous-jacent sur les segments du régime :
        elle sert de repère, pas de mesure de la stratégie. Gagner +2 % pendant que le marché fait
        +30 % n&apos;est pas une réussite.
      </p>
    </div>
  );
}

export function OptimizerValidatePanel({
  jobId,
  disabled,
  initialCapital,
}: {
  jobId: string;
  disabled?: boolean;
  initialCapital?: number;
}) {
  const valider = useOptimizeValidate();
  const [pending, setPending] = useState<Methode | null>(null);
  const [mc, setMc] = useState<OptimizeValidateResult | null>(null);
  const [regime, setRegime] = useState<OptimizeValidateResult | null>(null);

  const lancer = async (m: Methode) => {
    setPending(m);
    try {
      const res = await valider.mutateAsync({ jobId, method: m });
      if (m === 'monte_carlo') setMc(res);
      else setRegime(res);
    } catch (e) {
      toast.error(`Validation impossible : ${errorMessage(e) || 'erreur inconnue'}`);
    } finally {
      setPending(null);
    }
  };

  const enCours = valider.isPending;

  return (
    <Card className="border-l-2 border-l-cyan-500/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <FlaskConical className="w-4 h-4 text-cyan-400" />
          Valider ce paramétrage
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted">
          Rejoue les meilleurs paramètres pour éprouver leur robustesse. Chaque lancement relance
          un backtest complet côté serveur. Les résultats restent affichés une fois calculés.
        </p>

        {(!mc || !regime) && (
          <div className="flex flex-wrap gap-2">
            {!mc && (
              <Button
                size="sm" variant="outline" disabled={disabled || enCours}
                onClick={() => lancer('monte_carlo')}
              >
                {enCours && pending === 'monte_carlo'
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <Dices className="w-3.5 h-3.5" />}
                Monte-Carlo
              </Button>
            )}
            {!regime && (
              <Button
                size="sm" variant="outline" disabled={disabled || enCours}
                onClick={() => lancer('regime')}
              >
                {enCours && pending === 'regime'
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <LineChart className="w-3.5 h-3.5" />}
                Par régime de marché
              </Button>
            )}
          </div>
        )}

        {mc && (
          <MonteCarloPanel
            data={(mc.result ?? {}) as MonteCarloData}
            initialCapital={initialCapital}
            nTrades={mc.n_trades}
          />
        )}
        {regime && (
          <div className="space-y-2 pt-1">
            <div className="text-[10px] uppercase tracking-wide text-dim">
              Performance par régime
              {typeof regime.n_trades === 'number' && ` · ${regime.n_trades} trades`}
            </div>
            <ResultatRegimes data={regime} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
