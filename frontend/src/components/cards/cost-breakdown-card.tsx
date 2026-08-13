/**
 * L0/L2 — décomposition brut → net d'un backtest.
 *
 * `total_pnl` et `net_profit` ne mesurent pas la même chose : le premier somme
 * les PnL de clôture, le second est la variation d'équité réelle. L'écart vaut
 * exactement les frais d'entrée, prélevés à l'ouverture
 * (cf. docs/MESURE_GEOMETRIE_SORTIE.md §3). Publier les deux côte à côte est le
 * seul moyen de ne pas confondre un profit factor > 1 avec un résultat positif —
 * ce qui est arrivé sur BTC 4 h : PF 1,147 pour un PnL net de −0,33 %.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { BacktestResult } from '@/types';

interface Props {
  result?: BacktestResult | null;
}

const fmt = (v: number | undefined, signe = false) =>
  v == null ? '—' : `${signe && v > 0 ? '+' : ''}${v.toFixed(2)}`;

export function CostBreakdownCard({ result }: Props) {
  if (!result || result.gross_profit == null) return null;

  const brut = result.gross_profit ?? 0;
  const frais = result.total_fees ?? 0;
  const borrow = result.total_borrow_cost ?? 0;
  const funding = result.total_funding_cost ?? 0;
  const slippage = result.total_slippage_cost ?? 0;
  const entree = result.total_entry_fees ?? 0;
  const net = result.net_profit ?? 0;
  const totalPnl = result.total_pnl ?? 0;
  const ecart = totalPnl - net;

  const ligne = (label: string, valeur: number | undefined, aide?: string,
                 accent = '') => (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-xs text-muted-foreground">
        {label}
        {aide && <span className="ml-1 opacity-60">({aide})</span>}
      </span>
      <span className={`font-mono text-xs ${accent}`}>{fmt(valeur, true)}</span>
    </div>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">💸 Du brut au net</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        {ligne('PnL brut', brut, 'directionnel, avant tout coût',
               brut >= 0 ? 'text-emerald-400' : 'text-rose-400')}
        <div className="border-t border-border/50 my-1" />
        {ligne('Frais', -frais, 'entrée + sortie + impact', 'text-rose-400')}
        {slippage > 0 && ligne('dont slippage', -slippage, 'spread + impact',
                               'text-rose-400/70')}
        {borrow > 0 && ligne('Emprunt margin', -borrow, undefined, 'text-rose-400')}
        {funding !== 0 && ligne('Funding perp', -funding,
                                funding < 0 ? 'encaissé' : 'payé',
                                funding < 0 ? 'text-emerald-400' : 'text-rose-400')}
        <div className="border-t border-border my-1" />
        {ligne('PnL net (équité)', net, 'final − initial',
               net >= 0 ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold')}
        {Math.abs(ecart) > 0.005 && (
          <div className="mt-2 pt-2 border-t border-border/50 text-[0.65rem] text-muted-foreground leading-relaxed">
            ⚠ <span className="font-mono">total_pnl</span> vaut{' '}
            <span className="font-mono">{fmt(totalPnl, true)}</span>, soit{' '}
            <span className="font-mono">{fmt(ecart, true)}</span> de plus que le net :
            il ne retranche pas les frais d&apos;entrée
            {entree > 0 && <> (<span className="font-mono">{fmt(entree)}</span>)</>},
            prélevés à l&apos;ouverture. C&apos;est le net qui fait foi.
          </div>
        )}
      </CardContent>
    </Card>
  );
}
