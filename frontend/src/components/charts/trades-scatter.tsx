'use client';

/**
 * E3-F2-US3 — Scatter des trades (prix × temps).
 *
 * Reprend le scatter Chart.js de l'ancienne `backtest.html`, en recharts (la
 * lib analytique déjà standardisée côté Next.js — pas de 3e librairie).
 *
 * Contrat réel d'un trade de backtest (app/engine/backtest.py, position à
 * l'ouverture puis `position.update()` à la clôture) :
 *   { id, symbol, side, strategy, score, entry, stop, take_profit, size,
 *     notional, bar, entry_time, fees, status, pnl, exit, exit_time, exit_bar,
 *     exit_reason, pnl_pct, duration_bars }
 *
 * `entry_time` vaut `str(df["time"][i])` : selon la source c'est un ISO ou un
 * epoch ms sérialisé. Les deux sont acceptés, avec repli sur l'index de barre
 * (`bar`) quand la date est inexploitable — sans quoi tous les points
 * s'empileraient sur x=0.
 */

import { useMemo, useState } from 'react';
import {
  CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, Legend,
} from 'recharts';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { ScatterChart as ScatterIcon } from 'lucide-react';
import { formatUSD } from '@/lib/utils';

interface Trade {
  side?: string;
  strategy?: string;
  entry?: number;
  exit?: number;
  entry_time?: string | number;
  exit_time?: string | number;
  bar?: number;
  pnl?: number;
  pnl_pct?: number;
  exit_reason?: string;
  duration_bars?: number;
}

interface Point {
  x: number;
  y: number;
  pnl: number;
  pnlPct: number;
  side: string;
  strategy: string;
  reason: string;
  label: string;
}

/** Accepte ISO, epoch (s ou ms) ou chaîne numérique ; NaN si inexploitable. */
function toEpoch(v: string | number | undefined): number {
  if (v === null || v === undefined) return NaN;
  if (typeof v === 'number') return v < 1e11 ? v * 1000 : v;
  const asNum = Number(v);
  if (Number.isFinite(asNum) && v.trim() !== '') return asNum < 1e11 ? asNum * 1000 : asNum;
  const parsed = Date.parse(v);
  return Number.isFinite(parsed) ? parsed : NaN;
}

export function TradesScatter({ trades, symbol }: { trades: Trade[]; symbol?: string }) {
  const [useBarIndex, setUseBarIndex] = useState(false);

  const { wins, losses, timeMode } = useMemo(() => {
    const list = Array.isArray(trades) ? trades : [];
    // Si aucune date n'est exploitable, on bascule sur l'index de barre pour
    // tout le graphique — mélanger les deux échelles n'aurait aucun sens.
    const anyValidDate = list.some((t) => Number.isFinite(toEpoch(t.entry_time)));
    const byIndex = useBarIndex || !anyValidDate;

    const pts: Point[] = list
      .map((t, i) => {
        const epoch = toEpoch(t.entry_time);
        const x = byIndex ? Number(t.bar ?? i) : epoch;
        const y = Number(t.entry ?? 0);
        if (!Number.isFinite(x) || !Number.isFinite(y) || y === 0) return null;
        return {
          x,
          y,
          pnl: Number(t.pnl ?? 0),
          pnlPct: Number(t.pnl_pct ?? 0),
          side: String(t.side ?? ''),
          strategy: String(t.strategy ?? ''),
          reason: String(t.exit_reason ?? ''),
          label: byIndex
            ? `barre ${t.bar ?? i}`
            : new Date(epoch).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }),
        } as Point;
      })
      .filter((p): p is Point => p !== null);

    return {
      wins: pts.filter((p) => p.pnl >= 0),
      losses: pts.filter((p) => p.pnl < 0),
      timeMode: byIndex ? ('bar' as const) : ('date' as const),
    };
  }, [trades, useBarIndex]);

  if (wins.length === 0 && losses.length === 0) return null;

  const fmtX = (v: number) =>
    timeMode === 'bar'
      ? String(v)
      : new Date(v).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <ScatterIcon className="w-3.5 h-3.5" />
          Trades — prix × temps{symbol ? ` (${symbol})` : ''}
        </CardTitle>
        <span className="text-[10px] text-dim">
          {wins.length} gagnants · {losses.length} perdants
        </span>
      </CardHeader>
      <CardContent>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis
                type="number"
                dataKey="x"
                domain={['dataMin', 'dataMax']}
                tickFormatter={fmtX}
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                name={timeMode === 'bar' ? 'Barre' : 'Date'}
              />
              <YAxis
                type="number"
                dataKey="y"
                domain={['auto', 'auto']}
                tickFormatter={(v: number) => formatUSD(v)}
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                name="Prix d'entrée"
                width={80}
              />
              <ZAxis type="number" range={[40, 40]} />
              <Tooltip
                cursor={{ strokeDasharray: '3 3' }}
                contentStyle={{
                  background: '#141a23',
                  border: '1px solid #1f2937',
                  borderRadius: 8,
                  fontSize: 11,
                }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0].payload as Point;
                  return (
                    <div className="rounded-lg border border-border bg-card p-2 text-[11px] space-y-0.5">
                      <div className="font-semibold">
                        {p.side.toUpperCase()} · {p.strategy || '—'}
                      </div>
                      <div className="text-muted">{p.label}</div>
                      <div className="font-mono">Entrée : {formatUSD(p.y)}</div>
                      <div className={p.pnl >= 0 ? 'text-emerald-400 font-mono' : 'text-red-400 font-mono'}>
                        PnL : {formatUSD(p.pnl)} ({p.pnlPct.toFixed(2)}%)
                      </div>
                      {p.reason && <div className="text-dim">Sortie : {p.reason}</div>}
                    </div>
                  );
                }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Scatter name="Gagnants" data={wins} fill="#10b981" fillOpacity={0.75} />
              <Scatter name="Perdants" data={losses} fill="#ef4444" fillOpacity={0.75} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
        {timeMode === 'date' && (
          <button
            type="button"
            onClick={() => setUseBarIndex(true)}
            className="mt-2 text-[10px] text-dim hover:text-muted underline"
          >
            Basculer sur l&apos;index de barre
          </button>
        )}
      </CardContent>
    </Card>
  );
}
