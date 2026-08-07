/**
 * RPL-003 — Signal log temps réel pendant le replay.
 *
 * Extrait du template Jinja2 `replay.html:235-238` + `replay.html:783-800`
 * (`addSignalLog`). Affiche une ligne par sortie de trade, plus récent en tête.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { SignalLogEntry } from '@/hooks/use-replay-engine';

interface Props {
  entries: SignalLogEntry[];
}

export function ReplaySignalLog({ entries }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          📜 Signal log
          {entries.length > 0 && (
            <Badge variant="muted" className="ml-2">
              {entries.length} sortie{entries.length > 1 ? 's' : ''}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="max-h-[110px] overflow-y-auto font-mono text-[0.7rem] space-y-0.5">
          {entries.length === 0 ? (
            <div className="text-muted-foreground italic">Aucune sortie de trade pour l&apos;instant…</div>
          ) : (
            entries.map((e, i) => (
              <div
                key={`${e.time}-${i}`}
                className="flex items-center gap-2 px-1 py-0.5 rounded hover:bg-muted/20"
              >
                <span className="text-muted-foreground">{e.ts}</span>
                <span className="text-dim">·</span>
                <Badge variant={e.side === 'long' ? 'success' : 'danger'} className="text-[0.6rem]">
                  {e.side.toUpperCase()}
                </Badge>
                <span className="text-dim">·</span>
                <span className="text-foreground">exit ${e.exitPrice.toFixed(4)}</span>
                <span className="text-dim">·</span>
                <span className={e.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                  PnL {e.pnl >= 0 ? '+' : ''}${e.pnl.toFixed(2)}
                </span>
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}
