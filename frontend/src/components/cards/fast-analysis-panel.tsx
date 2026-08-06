'use client';

/**
 * Panel Fast Analyse — grille IS/OOS des signaux indicateurs (+ SMC).
 * Partagé Scanner + Smart Graph.
 */

import { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Search, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '@/lib/utils';

export function FastAnalysisPanel({
  result,
  symbol,
  timeframe,
}: {
  result: any;
  symbol: string;
  timeframe: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  if (!result) return null;

  return (
    <Card>
      <CardHeader
        className="cursor-pointer"
        onClick={() => setCollapsed((c) => !c)}
      >
        <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
          <Search className="w-4 h-4 text-primary-400" />
          Fast Analyse · {symbol} · {timeframe}
          {result.best && (
            <Badge variant="success" className="text-[10px]">
              Meilleur OOS : {result.best}
            </Badge>
          )}
          {result.bars != null && (
            <span className="text-[10px] text-dim font-normal ml-auto">
              {result.bars} barres · OOS {result.oos_bars ?? '—'}
            </span>
          )}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setCollapsed((c) => !c); }}
            aria-label={collapsed ? 'Déplier Fast Analyse' : 'Replier Fast Analyse'}
            aria-expanded={!collapsed}
            className="p-0.5 rounded hover:bg-card-hover text-muted"
          >
            {collapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          </button>
        </CardTitle>
        <p className="text-[11px] text-muted font-normal">
          Chaque ligne = signal d&apos;indicateur testé en backtest simple (maker/taker, full vs OOS).
          WR déjà en % (0–100).
        </p>
      </CardHeader>
      {collapsed ? null : (
      <CardContent className="p-0">
        {result.error ? (
          <p className="text-xs text-red-400 p-4">{result.error}</p>
        ) : !Array.isArray(result.rows) || result.rows.length === 0 ? (
          <p className="text-xs text-muted p-4 text-center">
            Aucun résultat (historique &lt; 260 barres ou erreur de calcul)
          </p>
        ) : (
          <div className="overflow-x-auto max-h-80">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card z-10">
                <tr className="text-left text-dim border-b border-border">
                  <th className="p-2 font-medium">Signal</th>
                  <th className="p-2 font-medium">Famille</th>
                  <th className="p-2 font-medium text-right">OOS n</th>
                  <th className="p-2 font-medium text-right">OOS PnL</th>
                  <th className="p-2 font-medium text-right">OOS PF</th>
                  <th className="p-2 font-medium text-right">OOS WR</th>
                  <th className="p-2 font-medium text-right">Full PnL</th>
                  <th className="p-2 font-medium">Edge</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 24).map((row: any, i: number) => {
                  const oos = row.maker?.oos || {};
                  const full = row.maker?.full || {};
                  const pnl = Number(oos.pnl ?? 0);
                  const isBest = result.best && row.signal === result.best;
                  return (
                    <tr
                      key={`${row.signal}-${i}`}
                      className={cn(
                        'border-b border-border/30',
                        isBest && 'bg-emerald-500/10',
                      )}
                    >
                      <td className="p-2 font-mono max-w-[14rem] truncate" title={row.signal}>
                        {row.signal}
                      </td>
                      <td className="p-2">
                        <Badge variant="muted" className="text-[9px]">{row.kind || '—'}</Badge>
                      </td>
                      <td className="p-2 text-right font-mono">{oos.n ?? '—'}</td>
                      <td className={cn(
                        'p-2 text-right font-mono font-semibold',
                        pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-muted',
                      )}>
                        {Number.isFinite(pnl) ? pnl.toFixed(1) : '—'}
                      </td>
                      <td className="p-2 text-right font-mono">
                        {oos.pf != null ? Number(oos.pf).toFixed(2) : '—'}
                      </td>
                      <td className="p-2 text-right font-mono">
                        {oos.wr != null ? `${Number(oos.wr).toFixed(0)}%` : '—'}
                      </td>
                      <td className="p-2 text-right font-mono text-dim">
                        {full.pnl != null ? Number(full.pnl).toFixed(1) : '—'}
                      </td>
                      <td className="p-2 text-dim text-[10px]">{row.edge || (isBest ? '★' : '—')}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
      )}
    </Card>
  );
}
