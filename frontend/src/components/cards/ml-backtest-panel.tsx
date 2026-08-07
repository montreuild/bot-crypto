/**
 * BT-010 — Panneau ML spécifique aux stratégies `ml_*`.
 *
 * Extrait du template Jinja2 `backtest.html:575-580` (`mlHTML`).
 * 4 KPIs (AUC, n_features, lookahead, proba_up) + warning si 0 trades.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { normalizeMlInfo } from '@/lib/backend-normalizers';

interface Props {
  mlInfo: any;
  strategy: string;
  nTrades: number;
}

function aucColor(auc: number | null): string {
  if (auc == null) return 'text-muted-foreground';
  if (auc >= 0.6) return 'text-emerald-400';
  if (auc >= 0.5) return 'text-amber-400';
  return 'text-rose-400';
}

export function MLBacktestPanel({ mlInfo, strategy, nTrades }: Props) {
  // Masqué pour les stratégies non-ML.
  if (!strategy.startsWith('ml_') && !mlInfo) return null;

  const info = normalizeMlInfo(mlInfo, strategy);
  if (!info) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          🧠 Stratégie ML{info.model_version ? ` — ${info.model_version}` : ''}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-4 gap-2">
          <div className="bg-surface border border-border rounded-md px-3 py-2">
            <div className="text-[0.6rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
              AUC cross-val
            </div>
            <div className={`font-mono text-sm font-bold ${aucColor(info.auc)}`}>
              {info.auc != null ? info.auc.toFixed(3) : '—'}
            </div>
          </div>
          <div className="bg-surface border border-border rounded-md px-3 py-2">
            <div className="text-[0.6rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
              Nb features
            </div>
            <div className="font-mono text-sm font-bold">{info.n_features ?? '—'}</div>
          </div>
          <div className="bg-surface border border-border rounded-md px-3 py-2">
            <div className="text-[0.6rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
              Lookahead
            </div>
            <div className="font-mono text-sm font-bold">
              {info.lookahead != null ? `${info.lookahead} bar` : '—'}
            </div>
          </div>
          <div className="bg-surface border border-border rounded-md px-3 py-2">
            <div className="text-[0.6rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
              Proba haussière moy.
            </div>
            <div className="font-mono text-sm font-bold">
              {info.proba_up != null ? info.proba_up.toFixed(3) : '—'}
            </div>
          </div>
        </div>

        {nTrades === 0 && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            ⚠ Aucun signal ML généré — le modèle n&apos;a pas pu produire de trades. Causes possibles :
            données insuffisantes, filtre ADX trop restrictif, seuil de probabilité trop élevé.
            Essayez avec ≥ 2000 bougies.
          </div>
        )}

        <div className="text-xs text-muted-foreground italic">
          Le modèle est réentraîné périodiquement selon <code className="font-mono">retrain_every</code>.
        </div>
      </CardContent>
    </Card>
  );
}
