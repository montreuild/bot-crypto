'use client';

/**
 * E3-F2-US1 — Table Walk-Forward avec folds OOS.
 *
 * Contrat réel de `WalkForwardAnalyzer.run()` (app/engine/walk_forward.py), et
 * NON celui que `/lab` supposait jusqu'ici. La page lisait
 * `walk_forward.folds` et `walk_forward.oos_pnl` : ces deux champs n'existent
 * pas, si bien que le résumé affichait « 0 folds · OOS PnL: $0.00 » en
 * permanence. Même classe de bug que R15-R17 (contrat d'API imaginé, invisible
 * à `tsc` puisque `api.ts` type tout en `any`).
 *
 * Succès :
 *   { n_folds, avg_oos_pnl, avg_oos_sharpe, avg_oos_wr, consistency,
 *     in_sample: BacktestResult[], out_of_sample: BacktestResult[] }
 * Échec :
 *   { error, n_bars?, fold_n?, min_required? }
 *
 * Chaque entrée de `out_of_sample` est un `BacktestResult.to_dict()` complet —
 * on en tire total_pnl / win_rate / sharpe / total_trades / max_drawdown.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { AlertTriangle, Layers } from 'lucide-react';
import { cn, formatUSD } from '@/lib/utils';

interface FoldResult {
  total_pnl?: number;
  win_rate?: number;
  sharpe?: number;
  total_trades?: number;
  max_drawdown?: number;
  profit_factor?: number;
}

export interface WalkForwardData {
  error?: string;
  n_bars?: number;
  fold_n?: number;
  min_required?: number;
  n_folds?: number;
  avg_oos_pnl?: number;
  avg_oos_sharpe?: number;
  avg_oos_wr?: number;
  consistency?: number;
  in_sample?: FoldResult[];
  out_of_sample?: FoldResult[];
}

export function WalkForwardTable({ data }: { data: WalkForwardData }) {
  // Le backend renvoie `{error}` quand les données sont insuffisantes — on
  // affiche le message tel quel, il porte déjà le seuil `min_required`.
  if (data?.error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Layers className="w-3.5 h-3.5" />
            Walk-Forward
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

  const folds = Array.isArray(data?.out_of_sample) ? data.out_of_sample : [];
  const inSample = Array.isArray(data?.in_sample) ? data.in_sample : [];
  if (folds.length === 0) return null;

  const consistency = Number(data.consistency ?? 0);
  // « Consistency » = % de folds OOS profitables. En dessous de 50 %, la
  // stratégie perd de l'argent sur la majorité des fenêtres hors-échantillon.
  const consistencyTone =
    consistency >= 70 ? 'text-emerald-400'
      : consistency >= 50 ? 'text-amber-400'
        : 'text-red-400';

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Layers className="w-3.5 h-3.5" />
          Walk-Forward — {data.n_folds ?? folds.length} folds OOS
        </CardTitle>
        <Badge variant={consistency >= 50 ? 'success' : 'danger'}>
          {consistency.toFixed(0)}% de folds gagnants
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Moyennes OOS */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <div>
            <div className="text-dim">PnL OOS moyen</div>
            <div className={cn('font-mono font-semibold', Number(data.avg_oos_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
              {formatUSD(Number(data.avg_oos_pnl ?? 0))}
            </div>
          </div>
          <div>
            <div className="text-dim">Sharpe OOS moyen</div>
            <div className="font-mono font-semibold">{Number(data.avg_oos_sharpe ?? 0).toFixed(2)}</div>
          </div>
          <div>
            <div className="text-dim">Win rate OOS moyen</div>
            <div className="font-mono font-semibold">{Number(data.avg_oos_wr ?? 0).toFixed(1)}%</div>
          </div>
          <div>
            <div className="text-dim">Régularité</div>
            <div className={cn('font-mono font-semibold', consistencyTone)}>{consistency.toFixed(1)}%</div>
          </div>
        </div>

        {/* Détail fold par fold */}
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <caption className="sr-only">
              Résultats hors-échantillon par fold du Walk-Forward
            </caption>
            <thead>
              <tr className="text-left text-dim border-b border-border">
                <th scope="col" className="p-2 font-medium">Fold</th>
                <th scope="col" className="p-2 font-medium text-right">PnL OOS</th>
                <th scope="col" className="p-2 font-medium text-right">Win rate</th>
                <th scope="col" className="p-2 font-medium text-right">Sharpe</th>
                <th scope="col" className="p-2 font-medium text-right">Trades</th>
                <th scope="col" className="p-2 font-medium text-right">Max DD</th>
                <th scope="col" className="p-2 font-medium text-right">PnL IS</th>
              </tr>
            </thead>
            <tbody>
              {folds.map((f, i) => {
                const pnl = Number(f.total_pnl ?? 0);
                // Le PnL in-sample du même fold : un écart massif IS ≫ OOS est
                // la signature d'un surapprentissage.
                const isPnl = inSample[i] ? Number(inSample[i].total_pnl ?? 0) : null;
                return (
                  <tr key={i} className="border-b border-border/30 hover:bg-card-hover">
                    <th scope="row" className="p-2 font-medium text-left text-muted">#{i + 1}</th>
                    <td className={cn('p-2 text-right font-mono', pnl >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                      {formatUSD(pnl)}
                    </td>
                    <td className="p-2 text-right font-mono">{Number(f.win_rate ?? 0).toFixed(1)}%</td>
                    <td className="p-2 text-right font-mono">{Number(f.sharpe ?? 0).toFixed(2)}</td>
                    <td className="p-2 text-right font-mono text-muted">{f.total_trades ?? 0}</td>
                    <td className="p-2 text-right font-mono text-red-400">
                      {Number(f.max_drawdown ?? 0).toFixed(2)}%
                    </td>
                    <td className="p-2 text-right font-mono text-dim">
                      {isPnl === null ? '—' : formatUSD(isPnl)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
