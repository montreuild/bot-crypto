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

        {/* Fuite temporelle : le backtest a chargé un modèle dont la fenêtre
            d'entraînement recouvre la fenêtre évaluée. Le serveur le détecte
            depuis toujours (`model_registry.overlaps`) mais ne le disait qu'au
            log — un résultat flatteur pouvait donc être lu comme valide.
            La source est l'artefact RÉELLEMENT chargé, pas la version active
            du moment : c'est ce qui rend l'avertissement exact. */}
        {info.overlap_warning && (
          <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300 space-y-1">
            <div className="font-semibold">⚠ Fuite temporelle probable</div>
            <div>
              Le modèle {info.model_version ? <code className="font-mono">{info.model_version}</code> : 'chargé'}
              {info.train_start && info.train_end
                ? <> a été entraîné sur <span className="font-mono">{info.train_start} → {info.train_end}</span>, période qui recouvre la fenêtre backtestée.</>
                : <> a été entraîné sur une période qui recouvre la fenêtre backtestée.</>}
              {' '}Il a donc déjà vu une partie des données sur lesquelles il est évalué :
              ces performances sont optimistes et ne se reproduiront pas en live.
            </div>
            <div className="text-rose-300/80">
              Backtestez une fenêtre postérieure à <span className="font-mono">{info.train_end ?? "l'entraînement"}</span>,
              ou entraînez un modèle sur une fenêtre antérieure.
            </div>
          </div>
        )}

        {info.fallback_to_inline && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            ⚠ Aucun modèle publié n&apos;était résoluble pour ce timeframe : la stratégie s&apos;est
            entraînée en ligne pendant le backtest. Le résultat ne reflète pas le modèle figé
            qui tournerait en live.
          </div>
        )}

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
