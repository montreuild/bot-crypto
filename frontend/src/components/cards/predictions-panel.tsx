'use client';

/**
 * E3-F4-US4 — Panel « Prédictions par stratégie ».
 *
 * Reprend le panel dédié de l'ancienne `scanner.html`. Le hook `useSignals`, la
 * méthode `api.getSignals` et le schéma zod existaient déjà (livrés en S8) mais
 * n'étaient montés NULLE PART : l'endpoint `/api/scanner/signals` était donc
 * consommé sur le papier et invisible en pratique — même situation que
 * `ExportButtons` (R19).
 *
 * Contrat réel de `build_signals_payload` (app/api/services/scanner_service.py) :
 *   { symbol, timeframe, signals: [{
 *       strategy, side, score, reason, skipped, active,
 *       setup?, setup_priority?, regime_lbl?, p_event?, p_up?,
 *       sl_atr_mult?, tp_atr_mult?, exit_after_bars?, size_factor?,
 *       tf_detected?, exit_td_active?, indicators?: {adx, rsi, auc_amp, auc_dir, n_features}
 *   }] }
 *
 * `skipped: true` signale une stratégie ML sans modèle entraîné — c'est une
 * information utile (il faut lancer un entraînement), pas une erreur à masquer.
 */

import { useMemo, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Brain } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface StrategySignal {
  strategy?: string;
  side?: string;
  score?: number | null;
  reason?: string;
  skipped?: boolean;
  active?: boolean;
  setup?: string | null;
  regime_lbl?: string | null;
  p_event?: number | null;
  p_up?: number | null;
  indicators?: Record<string, number | null> | null;
}

function sideStyle(side?: string) {
  if (side === 'long') return { label: 'LONG', cls: 'text-emerald-400' };
  if (side === 'short') return { label: 'SHORT', cls: 'text-red-400' };
  return { label: '—', cls: 'text-dim' };
}

/** Probabilité affichée en % ; le backend renvoie une fraction 0-1. */
function pct(v: number | null | undefined): string | null {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return null;
  return `${(Number(v) * 100).toFixed(1)}%`;
}

export function PredictionsPanel({
  signals,
  symbol,
  timeframe,
}: {
  signals: StrategySignal[];
  symbol?: string;
  timeframe?: string;
}) {
  const [activeOnly, setActiveOnly] = useState(false);

  const rows = useMemo(() => {
    const list = Array.isArray(signals) ? signals : [];
    const filtered = activeOnly ? list.filter((s) => s.active) : list;
    // Les stratégies qui émettent un signal d'abord, puis par score décroissant.
    return [...filtered].sort((a, b) => {
      const aSig = a.side === 'long' || a.side === 'short' ? 1 : 0;
      const bSig = b.side === 'long' || b.side === 'short' ? 1 : 0;
      if (aSig !== bSig) return bSig - aSig;
      return Number(b.score ?? 0) - Number(a.score ?? 0);
    });
  }, [signals, activeOnly]);

  if (rows.length === 0 && !activeOnly) return null;

  const withSignal = rows.filter((s) => s.side === 'long' || s.side === 'short').length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Brain className="w-3.5 h-3.5" />
          Prédictions par stratégie
          {symbol && <span className="text-dim font-normal">· {symbol} {timeframe}</span>}
        </CardTitle>
        <div className="flex items-center gap-3">
          <Badge variant={withSignal > 0 ? 'success' : 'muted'}>
            {withSignal} signal{withSignal > 1 ? 'aux' : ''}
          </Badge>
          <label className="flex items-center gap-1.5 text-[10px] text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(e) => setActiveOnly(e.target.checked)}
              className="accent-cyan-500"
            />
            Stratégies actives seulement
          </label>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-xs">
            <caption className="sr-only">
              Signal courant de chaque stratégie sur {symbol} {timeframe}
            </caption>
            <thead className="sticky top-0 bg-card">
              <tr className="text-left text-dim border-b border-border">
                <th scope="col" className="p-2 font-medium">Stratégie</th>
                <th scope="col" className="p-2 font-medium">Sens</th>
                <th scope="col" className="p-2 font-medium text-right">Score</th>
                <th scope="col" className="p-2 font-medium">Setup</th>
                <th scope="col" className="p-2 font-medium">Régime</th>
                <th scope="col" className="p-2 font-medium text-right" title="Probabilité qu'un évènement se produise">P(évt)</th>
                <th scope="col" className="p-2 font-medium text-right" title="Probabilité de hausse">P(haus.)</th>
                <th scope="col" className="p-2 font-medium">Raison</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s, i) => {
                const sd = sideStyle(s.side);
                const pEvent = pct(s.p_event);
                const pUp = pct(s.p_up);
                return (
                  <tr
                    key={`${s.strategy}-${i}`}
                    className={cn(
                      'border-b border-border/30 hover:bg-card-hover',
                      s.skipped && 'opacity-60',
                    )}
                  >
                    <th scope="row" className="p-2 font-mono font-medium text-left">
                      <span className="flex items-center gap-1.5">
                        {s.strategy || '—'}
                        {!s.active && <Badge variant="muted">inactive</Badge>}
                      </span>
                    </th>
                    <td className={cn('p-2 font-semibold', sd.cls)}>{sd.label}</td>
                    <td className="p-2 text-right font-mono">
                      {s.score === null || s.score === undefined ? '—' : Number(s.score).toFixed(3)}
                    </td>
                    <td className="p-2 font-mono text-cyan-400">{s.setup || '—'}</td>
                    <td className="p-2 text-muted">{s.regime_lbl || '—'}</td>
                    <td className="p-2 text-right font-mono text-muted">{pEvent ?? '—'}</td>
                    <td
                      className={cn(
                        'p-2 text-right font-mono',
                        pUp === null ? 'text-muted'
                          : Number(s.p_up) >= 0.5 ? 'text-emerald-400' : 'text-red-400',
                      )}
                    >
                      {pUp ?? '—'}
                    </td>
                    <td className="p-2 text-muted truncate max-w-[18rem]" title={s.reason || ''}>
                      {s.reason || '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && (
          <p className="p-4 text-xs text-muted">Aucune stratégie active sur ce symbole.</p>
        )}
      </CardContent>
    </Card>
  );
}
