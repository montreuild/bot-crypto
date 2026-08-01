'use client';

/**
 * Vue par bot — bots dont l'edge est statistiquement significatif
 * (`GET /api/bots`, champ `edge_significant`).
 *
 * Lot Portefeuille : cette table vivait dans `/portfolio` et était la seconde
 * des deux raisons pour lesquelles sa 308 vers `/portfolio-v2` restait
 * bloquée. `/bots-v2` liste bien tous les bots, mais ne filtre pas sur
 * l'edge : le portefeuille est le seul endroit qui répond à « lesquels
 * gagnent vraiment de l'argent, hors bruit ? ».
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD, parseSlotKey, lifecycleStyle } from '@/lib/utils';
import { useBots } from '@/hooks/use-api';
import { Bot as BotIcon } from 'lucide-react';

export function SignificantBotsTable({ fallbackBots }: { fallbackBots?: any[] }) {
  const { data: botsData } = useBots();
  const bots = botsData?.bots || fallbackBots || [];
  const significantBots = bots.filter((b: any) => b?.edge_significant);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Bots avec edge significatif</CardTitle>
        <Badge variant="purple">{significantBots.length}</Badge>
      </CardHeader>
      <CardContent className="p-0">
        {significantBots.length === 0 ? (
          <div className="text-sm text-muted text-center py-6">
            Aucun bot avec edge significatif actuellement
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-dim border-b border-border">
                  <th scope="col" className="p-3 font-medium">Slot</th>
                  <th scope="col" className="p-3 font-medium">État</th>
                  <th scope="col" className="p-3 font-medium text-right">Budget %</th>
                  <th scope="col" className="p-3 font-medium text-right">Used %</th>
                  <th scope="col" className="p-3 font-medium text-right">Edge CI Low</th>
                  <th scope="col" className="p-3 font-medium text-right">Trades</th>
                  <th scope="col" className="p-3 font-medium text-right">Weekly PnL</th>
                </tr>
              </thead>
              <tbody>
                {significantBots.map((bot: any) => {
                  const { strategy, tf, symbol } = parseSlotKey(bot.slot_key);
                  const style = lifecycleStyle(bot.state);
                  const edge = bot.edge || {};
                  const budget = bot.budget || {};
                  return (
                    <tr key={bot.slot_key} className="border-b border-border/30 hover:bg-card-hover">
                      <td className="p-3">
                        <div className="flex items-center gap-2">
                          <BotIcon className="w-3.5 h-3.5 text-primary-400" />
                          <div>
                            <div className="font-mono font-semibold text-xs">{strategy}</div>
                            <div className="text-[10px] text-dim">{tf} · {symbol || '—'}</div>
                          </div>
                        </div>
                      </td>
                      <td className="p-3">
                        <Badge variant={
                          bot.state === 'actif' ? 'success'
                            : bot.state === 'essai' ? 'info'
                              : bot.state === 'retire' ? 'danger' : 'warning'
                        }>
                          <span className={style.text}>{style.icon}</span>
                          {style.label}
                        </Badge>
                      </td>
                      <td className="p-3 text-right font-mono">{(budget.budget_pct ?? 0).toFixed(1)}%</td>
                      <td className="p-3 text-right font-mono text-muted">{(budget.used_pct ?? 0).toFixed(1)}%</td>
                      <td className={cn('p-3 text-right font-mono font-semibold', (edge.ci_low_pct ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400')}>
                        {(edge.ci_low_pct ?? 0).toFixed(2)}%
                      </td>
                      <td className="p-3 text-right font-mono text-muted">{edge.n ?? 0}</td>
                      <td className={cn('p-3 text-right font-mono', (budget.weekly_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                        {(budget.weekly_pnl ?? 0) >= 0 ? '+' : ''}{formatUSD(budget.weekly_pnl ?? 0)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
