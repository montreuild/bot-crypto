/**
 * BT-004 — Banner "backtest déjà en cours côté serveur".
 *
 * Extrait du template Jinja2 `backtest.html:277-297` (`_syncBacktestRunState`).
 */

import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

interface Props {
  startedAt: string | null;
  onCancel: () => void;
}

export function BacktestRunningBanner({ startedAt, onCancel }: Props) {
  if (!startedAt) return null;
  return (
    <Card className="border-amber-500/30 bg-amber-500/5">
      <CardContent className="flex items-center justify-between py-3">
        <span className="text-xs text-amber-300">
          ⏳ Un backtest est déjà en cours côté serveur (démarré à {startedAt}).
        </span>
        <Button variant="ghost" size="sm" onClick={onCancel} className="h-7 text-xs text-amber-300 hover:text-amber-200">
          Annuler
        </Button>
      </CardContent>
    </Card>
  );
}
