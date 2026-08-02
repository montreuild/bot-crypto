/**
 * Contexte d'exécution facturé par un backtest ou une optimisation.
 *
 * ── Pourquoi ce composant existe ──────────────────────────────────────────
 * Un PnL seul n'est pas interprétable : le même paramétrage donne des chiffres
 * très différents selon que l'instrument est résolu sur une venue spot ou
 * margin (l'emprunt n'est facturé que sur margin), et selon le modèle de frais
 * (une action paie une commission fixe, un plancher de courtage et une taxe de
 * transaction que la crypto ignore). Afficher le résultat sans son contexte
 * laisse comparer de bonne foi deux runs incomparables.
 *
 * La donnée vient de `app/core/execution.py::cost_model` — mêmes sources que
 * les formules de coût elles-mêmes, donc pas de risque de dérive entre ce qui
 * est affiché et ce qui est facturé.
 */
'use client';

import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { CostModel } from '@/types';

const pct = (x: number | undefined, decimals = 3) =>
  x === undefined || x === null ? '—' : `${(x * 100).toFixed(decimals)} %`;

function Ligne({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-dim shrink-0">{label}</span>
      <span className="font-mono text-right">{children}</span>
    </div>
  );
}

export function CostModelCard({ model }: { model?: CostModel | null }) {
  if (!model) return null;

  const margin = model.market_type === 'margin' || model.market_type === 'perp';
  const actions = model.asset_class === 'equity';

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">Contexte facturé</span>
            <span className="font-mono text-xs text-muted">{model.venue}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Badge variant={margin ? 'warning' : 'default'}>
              {model.market_type}
              {model.margin_mode ? ` / ${model.margin_mode}` : ''}
            </Badge>
            <Badge variant={model.max_leverage > 1 ? 'warning' : 'default'}>
              levier ×{model.max_leverage}
            </Badge>
            <Badge variant="default">
              {model.asset_class} / {model.quote_currency}
            </Badge>
            {model.can_execute === false && <Badge variant="danger">data-only</Badge>}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 text-xs">
          {/* ── Frais ─────────────────────────────────────────────────────── */}
          <div>
            <div className="text-dim uppercase tracking-wide text-[10px] mb-1">Frais</div>
            <Ligne label="Taker">{pct(model.fee_rate_taker)}</Ligne>
            <Ligne label="Maker">{pct(model.fee_rate_maker)}</Ligne>
            {/* Frais fixes, plancher et taxe : propres aux actions, masqués en
                crypto où ils valent 0 et n'apprendraient rien. */}
            {!!model.fee_fixed && <Ligne label="Fixe / ordre">{model.fee_fixed.toFixed(2)}</Ligne>}
            {!!model.fee_min && <Ligne label="Plancher courtage">{model.fee_min.toFixed(2)}</Ligne>}
            {!!model.transaction_tax_pct && (
              <Ligne label={`Taxe transaction${model.tax_on_buy_only ? ' (achat)' : ''}`}>
                {pct(model.transaction_tax_pct)}
              </Ligne>
            )}
            <Ligne label="Spread">{pct(model.spread_pct)}</Ligne>
            <Ligne label="Slippage">
              {model.slippage_model}
              {model.slippage_model !== 'static' ? ` (k=${model.slippage_k})` : ''}
            </Ligne>
          </div>

          {/* ── Emprunt et contraintes ────────────────────────────────────── */}
          <div>
            <div className="text-dim uppercase tracking-wide text-[10px] mb-1">
              Emprunt & contraintes
            </div>
            {model.borrows ? (
              <>
                <Ligne label="Emprunt / jour">{pct(model.borrow_rate_daily, 4)}</Ligne>
                <Ligne label="Équivalent annuel">{pct(model.borrow_rate_annual, 1)}</Ligne>
                <Ligne label="Périodes / jour">{model.borrow_periods_per_day}</Ligne>
              </>
            ) : (
              // Dire explicitement qu'il n'y a pas d'emprunt : une ligne absente
              // se lit comme un oubli, pas comme une information.
              <Ligne label="Emprunt">
                <span className="text-emerald-400">aucun ({model.market_type})</span>
              </Ligne>
            )}
            <Ligne label="Fill partiel">{`${(model.partial_fill_pct * 100).toFixed(0)} %`}</Ligne>
            <Ligne label="Notionnel max">{`${(model.max_notional_pct * 100).toFixed(0)} % du capital`}</Ligne>
            {actions && (
              <>
                <Ligne label="Quantité">{model.fractional ? 'fractionnable' : 'entière'}</Ligne>
                {!!model.min_notional && (
                  <Ligne label="Notionnel min">{model.min_notional.toFixed(2)}</Ligne>
                )}
                {!!model.calendar && <Ligne label="Calendrier">{model.calendar}</Ligne>}
              </>
            )}
            {model.allow_short === false && <Ligne label="Vente à découvert">interdite</Ligne>}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
