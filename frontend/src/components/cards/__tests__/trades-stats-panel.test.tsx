/**
 * Les axes d'analyse produits par L0–L6 doivent être LISIBLES, pas seulement
 * présents dans le payload : un champ journalisé que l'interface n'affiche pas
 * est une mesure que personne ne lira.
 *
 * Les trades ci-dessous ont la forme que `Backtester._close_at` produit
 * réellement — état de structure, séquence, tier, classe de cible, jambes
 * partielles — et non une forme inventée pour le test.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TradesStatsPanel } from '@/components/cards/trades-stats-panel';
import type { BacktestTrade } from '@/types';

const BASE: BacktestTrade = {
  time: '2021-01-01T00:00:00', symbol: 'BTC/USDC', side: 'long',
  strategy: 'smart_money', entry: 100, exit: 102, pnl: 20, pnl_pct: 2,
  fees: 0.5, reason: 'LONG SWEEP_REVERSAL', status: 'closed',
  exit_reason: 'take_profit',
};

const SMC: BacktestTrade[] = [
  {
    ...BASE, id: 1, setup: 'SWEEP_REVERSAL', module: 'SMC_CORE',
    structure_state: 'BULLISH_PULLBACK', sequence_type: 'CONTINUATION',
    tier: 'A', indicators: { tp_class: 'PREV_WEEK' } as never,
  },
  {
    ...BASE, id: 2, pnl: -12, exit_reason: 'stop_loss',
    setup: 'OB_RETEST', module: 'SMC_CORE',
    structure_state: 'BEARISH_WARNING', sequence_type: 'EARLY_REVERSAL',
    tier: 'C', indicators: { tp_class: 'SWING' } as never,
    exits: [{ bar: 10, price: 101, size: 0.25, fraction: 0.25, reason: 'tp1', pnl: 3 }],
  },
];

/** Le cas de toutes les stratégies non-SMC : aucun champ nouveau. */
const NU: BacktestTrade[] = [{ ...BASE, id: 3 }];

describe('TradesStatsPanel — axes SMC', () => {
  it('affiche un tableau par axe renseigné', () => {
    render(<TradesStatsPanel trades={SMC} />);
    expect(screen.getByText('Par setup')).toBeInTheDocument();
    expect(screen.getByText(/Par module SMC\/ICT/)).toBeInTheDocument();
    expect(screen.getByText(/Par état de structure/)).toBeInTheDocument();
    expect(screen.getByText(/Par type de séquence/)).toBeInTheDocument();
    expect(screen.getByText(/Par tier/)).toBeInTheDocument();
    expect(screen.getByText(/Par classe de cible/)).toBeInTheDocument();
  });

  it('montre les valeurs et non seulement les en-têtes', () => {
    render(<TradesStatsPanel trades={SMC} />);
    expect(screen.getByText('BULLISH_PULLBACK')).toBeInTheDocument();
    expect(screen.getByText('EARLY_REVERSAL')).toBeInTheDocument();
    expect(screen.getByText('PREV_WEEK')).toBeInTheDocument();
  });

  it('compte les jambes partielles quand il y en a', () => {
    render(<TradesStatsPanel trades={SMC} />);
    expect(screen.getByText('Sorties partielles')).toBeInTheDocument();
    expect(screen.getByText('1 / 1 tr.')).toBeInTheDocument();
  });

  it("n'affiche aucun axe absent — le cas de toutes les stratégies non-SMC", () => {
    render(<TradesStatsPanel trades={NU} />);
    expect(screen.queryByText(/Par état de structure/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Par tier/)).not.toBeInTheDocument();
    expect(screen.queryByText('Sorties partielles')).not.toBeInTheDocument();
    // Les statistiques historiques, elles, restent affichées.
    expect(screen.getByText('Par raison de sortie')).toBeInTheDocument();
  });

  it('conserve les chips de base', () => {
    render(<TradesStatsPanel trades={SMC} />);
    expect(screen.getByText('WR Long')).toBeInTheDocument();
    expect(screen.getByText('Avg Win')).toBeInTheDocument();
  });
});
