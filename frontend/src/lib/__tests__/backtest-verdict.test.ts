/**
 * ARCH-02 — la règle du verdict, désormais testable.
 *
 * Ces seuils décident de ce qu'on dit à l'opérateur de sa stratégie, et
 * `positive` fait apparaître le bouton « Créer le bot (Essai) ». Ils vivaient
 * au milieu du JSX de `backtest-results.tsx` (708 lignes) : aucun test ne
 * pouvait les atteindre.
 */
import { describe, expect, it } from 'vitest';

import {
  SEUILS, bestStrategy, computeVerdict, strategyMap, unwrapBacktest,
} from '@/lib/backtest-verdict';

const strat = (o: Record<string, number>) => ({
  total_pnl: 0, win_rate: 0, sharpe: 0, max_drawdown: 0, total_trades: 0, ...o,
});
const res = (by: Record<string, any>) => ({ by_strategy: by } as any);

/** Le cas nominal « edge significatif » : tout au-dessus des seuils. */
const FORT = strat({
  total_pnl: 500, win_rate: 60, sharpe: 2.0, max_drawdown: -8, total_trades: 40,
});

describe('unwrapBacktest / strategyMap', () => {
  it('une passe multi-symboles rend un tableau — on prend le premier', () => {
    const a = { symbol: 'BTC' } as any;
    expect(unwrapBacktest([a, { symbol: 'ETH' } as any])).toBe(a);
    expect(unwrapBacktest(a)).toBe(a);
  });

  it('rend un objet vide plutôt que undefined', () => {
    expect(strategyMap({} as any)).toEqual({});
    expect(strategyMap(undefined as any)).toEqual({});
  });
});

describe('bestStrategy', () => {
  it('classe au PnL total', () => {
    const best = bestStrategy({
      a: strat({ total_pnl: 10 }), b: strat({ total_pnl: 99 }), c: strat({ total_pnl: 50 }),
    });
    expect(best?.[0]).toBe('b');
  });

  it('un PnL absent compte comme zéro, pas comme le meilleur', () => {
    const best = bestStrategy({ sans: strat({}), avec: strat({ total_pnl: 1 }) });
    expect(best?.[0]).toBe('avec');
  });

  it('rend null sans stratégie', () => {
    expect(bestStrategy({})).toBeNull();
  });
});

describe('computeVerdict', () => {
  it('sans stratégie : neutre, sans nom, sans risque', () => {
    const v = computeVerdict(res({}));
    expect(v).toMatchObject({ tone: 'neutral', bestName: null, risky: false, trades: 0 });
  });

  it('edge significatif → positive', () => {
    expect(computeVerdict(res({ s: FORT }))).toMatchObject({
      tone: 'positive', risky: false, bestName: 's',
    });
  });

  it('positif mais pas fort → neutral', () => {
    const v = computeVerdict(res({ s: strat({
      total_pnl: 100, win_rate: 45, sharpe: 0.8, max_drawdown: -5, total_trades: 30,
    }) }));
    expect(v.tone).toBe('neutral');
  });

  it('PnL négatif sans risque → negative, mais pas « risqué »', () => {
    const v = computeVerdict(res({ s: strat({
      total_pnl: -50, win_rate: 40, sharpe: 0.2, max_drawdown: -5, total_trades: 30,
    }) }));
    expect(v).toMatchObject({ tone: 'negative', risky: false });
  });
});

describe('computeVerdict — le risque prime sur la force', () => {
  it('un Sharpe flatteur sur trop peu de trades ne se recommande pas', () => {
    const v = computeVerdict(res({ s: { ...FORT, total_trades: SEUILS.tradesRisque - 1 } }));
    expect(v).toMatchObject({ tone: 'negative', risky: true });
  });

  it('un drawdown au-delà du seuil non plus', () => {
    const v = computeVerdict(res({ s: { ...FORT, max_drawdown: SEUILS.drawdownRisque - 0.1 } }));
    expect(v).toMatchObject({ tone: 'negative', risky: true });
  });

  it('exactement au seuil de drawdown : pas encore risqué', () => {
    const v = computeVerdict(res({ s: { ...FORT, max_drawdown: SEUILS.drawdownRisque } }));
    expect(v.risky).toBe(false);
    expect(v.tone).toBe('positive');
  });

  it('exactement au plancher de trades : pas encore risqué', () => {
    const v = computeVerdict(res({ s: {
      ...FORT, total_trades: SEUILS.tradesRisque, sharpe: 0.5, total_pnl: 10,
    } }));
    expect(v).toMatchObject({ risky: false, tone: 'neutral' });
  });
});

describe('computeVerdict — chaque seuil de « fort » est nécessaire', () => {
  it.each([
    ['sharpe',    { sharpe: SEUILS.sharpeFort }],
    ['trades',    { total_trades: SEUILS.tradesFort - 1 }],
    ['win rate',  { win_rate: SEUILS.winRateFort - 1 }],
  ])('sous le seuil de %s, le verdict retombe à neutral', (_nom, ecart) => {
    const v = computeVerdict(res({ s: { ...FORT, ...ecart } }));
    expect(v.tone).toBe('neutral');
    expect(v.risky).toBe(false);
  });

  it('pile aux seuils de trades et de win rate, « fort » tient', () => {
    const v = computeVerdict(res({ s: {
      ...FORT, total_trades: SEUILS.tradesFort, win_rate: SEUILS.winRateFort,
    } }));
    expect(v.tone).toBe('positive');
  });
});

describe('computeVerdict — champs absents', () => {
  it('des statistiques vides ne lèvent pas et donnent des zéros', () => {
    const v = computeVerdict(res({ s: {} }));
    expect(v).toMatchObject({
      bestName: 's', pnl: 0, winRate: 0, sharpe: 0, maxDrawdown: 0, trades: 0,
    });
    // 0 trade < plancher : risqué, donc négatif.
    expect(v).toMatchObject({ risky: true, tone: 'negative' });
  });
});
