/**
 * RPL-004 — Stats accumulées pendant le replay (trades, WR, PnL).
 *
 * Extrait du template Jinja2 `replay.html:139-149` (stats panel sidebar).
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { ReplayAccumulatedStats, CandleRow } from '@/hooks/use-replay-engine';

interface Props {
  stats: ReplayAccumulatedStats;
  candles: CandleRow[];
  position: number;
  dateFrom?: string;
  dateTo?: string;
}

function kpi(label: string, value: string, color = '') {
  return (
    <div className="bg-surface border border-border rounded-md px-3 py-2">
      <div className="text-[0.6rem] uppercase tracking-wide text-muted-foreground font-semibold mb-1">
        {label}
      </div>
      <div className={`font-mono text-sm font-bold ${color}`}>{value}</div>
    </div>
  );
}

export function ReplayStatsPanel({ stats, candles, position, dateFrom, dateTo }: Props) {
  const total = candles.length;
  const currentBar = candles[position];
  const currentDate = currentBar
    ? new Date(currentBar.time * 1000).toLocaleString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';

  const wr = stats.trades > 0 ? (stats.wins / stats.trades) * 100 : 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">📈 Stats live</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="grid grid-cols-2 gap-2">
          {kpi('Bougies', total > 0 ? `${position + 1} / ${total}` : '—')}
          {kpi(
            'Période',
            dateFrom && dateTo
              ? `${dateFrom.slice(0, 10)} → ${dateTo.slice(0, 10)}`
              : currentDate,
          )}
          {kpi('Trades', String(stats.trades), stats.trades > 0 ? 'text-cyan-400' : '')}
          {kpi(
            'Win Rate',
            stats.trades > 0 ? `${wr.toFixed(1)}%` : '—',
            wr >= 50 ? 'text-emerald-400' : stats.trades > 0 ? 'text-rose-400' : '',
          )}
          {kpi(
            'PnL total',
            `${stats.pnl >= 0 ? '+' : ''}$${stats.pnl.toFixed(2)}`,
            stats.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400',
          )}
          {kpi('Barre courante', currentDate)}
        </div>
      </CardContent>
    </Card>
  );
}
