/**
 * OPT-002 — Top-5 trials table + best params block.
 *
 * Extrait du template Jinja2 `optimizer.html:673-698`.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { OptimizeTrial } from '@/types';

interface Props {
  trials: OptimizeTrial[];
  bestParams: Record<string, number | string> | null;
}

function fmt(v: number | null | undefined, decimals = 3): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(decimals);
}

export function TopTrialsTable({ trials, bestParams }: Props) {
  if ((!trials || trials.length === 0) && (!bestParams || Object.keys(bestParams).length === 0)) {
    return null;
  }

  return (
    <div className="space-y-3">
      {bestParams && Object.keys(bestParams).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">⭐ Best params</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="max-h-[200px] overflow-y-auto font-mono text-xs space-y-0.5">
              {Object.entries(bestParams).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-cyan-400">{k}</span>
                  <span className="text-muted-foreground">:</span>
                  <span className="text-emerald-400 ml-2">
                    {typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(6)) : String(v)}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {trials && trials.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">🏆 Top-5 trials</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground">
                    <th className="text-left py-1 px-2">#</th>
                    <th className="text-right py-1 px-2">IS Score</th>
                    <th className="text-right py-1 px-2">OOS Score</th>
                    <th className="text-right py-1 px-2">Final</th>
                    <th className="text-right py-1 px-2">OOS PnL</th>
                    <th className="text-right py-1 px-2">OOS WR</th>
                    <th className="text-right py-1 px-2">OOS DD</th>
                    <th className="text-right py-1 px-2">Overfit</th>
                  </tr>
                </thead>
                <tbody>
                  {trials.slice(0, 5).map((t, i) => {
                    const final = t.final ?? t.final_score;
                    return (
                      <tr
                        key={i}
                        className={`border-b border-border/50 ${i === 0 ? 'bg-emerald-500/5' : ''}`}
                      >
                        <td className="py-1 px-2 font-mono">
                          {i === 0 ? '🏆' : ''} {i + 1}
                        </td>
                        <td className="text-right py-1 px-2 font-mono">{fmt(t.is_score)}</td>
                        <td className="text-right py-1 px-2 font-mono">{fmt(t.oos_score)}</td>
                        <td className="text-right py-1 px-2 font-mono font-bold">{fmt(final)}</td>
                        <td
                          className={`text-right py-1 px-2 font-mono ${
                            t.oos_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {t.oos_pnl >= 0 ? '+' : ''}${t.oos_pnl.toFixed(2)}
                        </td>
                        <td className="text-right py-1 px-2 font-mono">{(t.oos_wr * 100).toFixed(1)}%</td>
                        <td className="text-right py-1 px-2 font-mono text-rose-400">
                          -{(t.oos_dd * 100).toFixed(1)}%
                        </td>
                        <td
                          className={`text-right py-1 px-2 font-mono ${
                            t.overfit > 2 ? 'text-amber-400' : 'text-emerald-400'
                          }`}
                        >
                          {fmt(t.overfit)}
                          {t.overfit > 2 ? ' ⚠' : ' ✓'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
