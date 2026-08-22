'use client';

/** Rendu du verdict de backtest (ARCH-02) — la règle vit dans `backtest-verdict`. */
import { AlertCircle, CheckCircle2, Rocket, TrendingUp } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  computeVerdict, unwrapBacktest, type VerdictTone,
} from '@/lib/backtest-verdict';
import { formatMoney, quoteCurrency } from '@/lib/utils';
import type { BacktestResult } from '@/types';

const CADRE: Record<VerdictTone, string> = {
  positive: 'border-emerald-500/30 bg-emerald-500/5',
  neutral: 'border-cyan-500/30 bg-cyan-500/5',
  negative: 'border-red-500/30 bg-red-500/5',
};

export function Verdict({ result }: { result: BacktestResult | BacktestResult[] }) {
  const v = computeVerdict(result);
  const ccy = quoteCurrency(unwrapBacktest(result));

  if (v.bestName == null) {
    return (
      <Card className="border-amber-500/30 bg-amber-500/5">
        <CardContent className="p-4 flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-amber-400" />
          <p className="text-sm">
            <span className="font-medium">Pas de trades générés. </span>
            <span className="text-muted">Ajuste la période ou les stratégies sélectionnées.</span>
          </p>
        </CardContent>
      </Card>
    );
  }

  let icon = <AlertCircle className="w-5 h-5 text-red-400" />;
  let message = `${v.bestName} sous-performe `
    + `(${formatMoney(v.pnl, ccy, { sign: true })}, WR ${v.winRate.toFixed(0)}%). `
    + `Stratégie à revoir.`;

  if (v.tone === 'positive') {
    icon = <CheckCircle2 className="w-5 h-5 text-emerald-400" />;
    message = `Edge significatif sur ${v.bestName} avec ${v.trades} trades, `
      + `Sharpe ${v.sharpe.toFixed(2)}, max DD ${v.maxDrawdown.toFixed(1)}%. `
      + `Recommandation : essai avec budget 5%.`;
  } else if (v.tone === 'neutral') {
    icon = <TrendingUp className="w-5 h-5 text-cyan-400" />;
    message = `Résultats positifs sur ${v.bestName} `
      + `(${formatMoney(v.pnl, ccy, { sign: true })}, WR ${v.winRate.toFixed(0)}%) `
      + `mais edge limité. À valider par forward-test.`;
  } else if (v.risky) {
    icon = <AlertCircle className="w-5 h-5 text-amber-400" />;
    message = `Résultats risqués sur ${v.bestName} : ${v.trades} trades `
      + `(${v.trades < 10 ? 'trop peu' : 'ok'}), max DD ${v.maxDrawdown.toFixed(1)}%. `
      + `À éviter en l'état.`;
  }

  return (
    <Card className={CADRE[v.tone]}>
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 mt-0.5">{icon}</div>
          <div className="flex-1">
            <div className="text-sm font-medium mb-1">Verdict</div>
            <p className="text-sm text-foreground">{message}</p>
            {v.tone === 'positive' && (
              <Button
                size="sm"
                variant="success"
                className="mt-3"
                onClick={() => toast.info('Création du bot en essai — à implémenter')}
              >
                <Rocket className="w-3.5 h-3.5" />
                Créer le bot (Essai)
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
