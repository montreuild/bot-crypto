/**
 * BT-003 — Panneau Diagnostics de recherche de signaux.
 *
 * Extrait du template Jinja2 `backtest.html:582-597` (`diagHTML`).
 * Affiche 9 KPIs, 3 warnings contextuels, et un tableau per-strategy.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { BacktestDiagnostics } from '@/types';

interface Props {
  diagnostics: BacktestDiagnostics | null;
}

function kpi(label: string, value: string | number, tone?: 'green' | 'amber' | 'red') {
  const color = tone === 'green' ? 'text-emerald-400' : tone === 'amber' ? 'text-amber-400' : tone === 'red' ? 'text-rose-400' : '';
  return (
    <div className="bg-surface border border-border rounded-md px-3 py-2">
      <div className="text-[0.6rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
        {label}
      </div>
      <div className={`font-mono text-sm font-bold ${color}`}>{value}</div>
    </div>
  );
}

export function DiagnosticsPanel({ diagnostics }: Props) {
  if (!diagnostics) return null;
  const d = diagnostics;
  const barsTotal = d.bars_total ?? 0;
  if (!barsTotal) return null;

  const pctInPos = d.bars_in_position != null ? ((d.bars_in_position / barsTotal) * 100).toFixed(1) : '—';
  const pctSearch = d.bars_in_position != null ? (100 - (d.bars_in_position / barsTotal) * 100).toFixed(1) : '—';
  const sigAccepted = d.signals_accepted ?? 0;
  const tradesOpened = d.trades_opened ?? 0;
  const rejNotional = d.rejections?.notional ?? 0;
  const rejAtr = d.rejections?.atr_le_zero ?? 0;
  const maxBarsInPos = d.max_bars_in_position ?? 0;
  const maxBarsNoSig = d.max_bars_without_signal ?? 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">🔍 Diagnostics — recherche de signaux</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-3 gap-2">
          {kpi('Barres totales', barsTotal)}
          {kpi('% en position', `${pctInPos}%`)}
          {kpi('% recherche signal', `${pctSearch}%`)}
          {kpi('Signaux acceptés', sigAccepted, sigAccepted > 0 ? 'green' : 'red')}
          {kpi('Trades ouverts', tradesOpened)}
          {kpi('Rejets notional', rejNotional, rejNotional > 0 ? 'amber' : 'green')}
          {kpi('Rejets ATR≤0', rejAtr, rejAtr > 0 ? 'amber' : 'green')}
          {kpi('Max barres en pos.', maxBarsInPos, maxBarsInPos > 500 ? 'amber' : 'green')}
          {kpi('Max barres sans sig.', maxBarsNoSig)}
        </div>

        {/* Warnings contextuels */}
        <div className="space-y-2">
          {sigAccepted === 0 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              ⚠ Aucun signal accepté — vérifiez les filtres ADX, le score_threshold et les conditions de setup.
            </div>
          )}
          {maxBarsInPos > 500 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              ⚠ Position stuck &gt; 500 barres — vérifiez <code className="font-mono">disable_trailing</code> et{' '}
              <code className="font-mono">exit_after_bars</code> dans le YAML de la stratégie.
            </div>
          )}
          {sigAccepted > 0 && tradesOpened === 0 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              ⚠ Signaux acceptés mais 0 trade ouvert — rejets notional &lt; 1 USDC, vérifiez{' '}
              <code className="font-mono">risk_per_trade</code> et <code className="font-mono">capital</code>.
            </div>
          )}
        </div>

        {/* Tableau per-strategy */}
        {d.per_strategy && d.per_strategy.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-1 px-2">Stratégie</th>
                  <th className="text-right py-1 px-2" title="Nombre de bougies évaluées par la stratégie.">Évalué ?</th>
                  <th className="text-right py-1 px-2" title="Signaux neutres (pas de direction).">side=none</th>
                  <th className="text-right py-1 px-2" title="Signaux avec direction (long ou short).">Proposé</th>
                  <th className="text-right py-1 px-2" title="Signaux filtrés par score_threshold.">&lt; seuil</th>
                  <th className="text-right py-1 px-2" title="Signaux passés le seuil.">≥ seuil</th>
                  <th className="text-right py-1 px-2" title="Exceptions levées pendant l'évaluation.">Erreurs</th>
                </tr>
              </thead>
              <tbody>
                {d.per_strategy.map((s) => (
                  <tr key={s.strategy} className="border-b border-border/50">
                    <td className="py-1 px-2 font-mono">{s.strategy}</td>
                    <td className="text-right py-1 px-2 font-mono">{s.evaluated ?? '—'}</td>
                    <td className="text-right py-1 px-2 font-mono text-muted-foreground">{s.none ?? '—'}</td>
                    <td className="text-right py-1 px-2 font-mono">{s.proposed ?? '—'}</td>
                    <td className="text-right py-1 px-2 font-mono text-muted-foreground">{s.below_threshold ?? '—'}</td>
                    <td className="text-right py-1 px-2 font-mono text-emerald-400">{s.above_threshold ?? '—'}</td>
                    <td className="text-right py-1 px-2 font-mono">
                      {s.errors ? <Badge variant="danger">{s.errors}</Badge> : '0'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
