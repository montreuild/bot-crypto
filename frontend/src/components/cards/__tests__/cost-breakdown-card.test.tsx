/**
 * L'écart entre `total_pnl` et `net_profit` doit être VISIBLE, pas seulement
 * présent dans le payload : c'est lui qui explique qu'un profit factor de 1,147
 * puisse coexister avec un PnL net de −0,33 % (BTC 4 h, L1).
 *
 * Les chiffres ci-dessous viennent d'un backtest réel sur BTC/USDC 4 h, pas
 * d'une invention : brut 123,62, frais d'entrée 55,88, net 17,60.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CostBreakdownCard } from '@/components/cards/cost-breakdown-card';
import type { BacktestResult } from '@/types';

const REEL = {
  gross_profit: 123.62,
  total_fees: 106.01,
  total_entry_fees: 55.88,
  total_slippage_cost: 55.92,
  total_borrow_cost: 0,
  total_funding_cost: 0,
  net_profit: 17.6,
  total_pnl: 73.48,
} as unknown as BacktestResult;

describe('CostBreakdownCard', () => {
  it('affiche le brut et le net', () => {
    render(<CostBreakdownCard result={REEL} />);
    expect(screen.getByText('PnL brut')).toBeInTheDocument();
    expect(screen.getByText('+123.62')).toBeInTheDocument();
    expect(screen.getByText('PnL net (équité)')).toBeInTheDocument();
    expect(screen.getByText('+17.60')).toBeInTheDocument();
  });

  it("explique l'écart entre total_pnl et net_profit", () => {
    render(<CostBreakdownCard result={REEL} />);
    expect(screen.getByText(/ne retranche pas les frais/)).toBeInTheDocument();
    // 73,48 − 17,60 = 55,88, exactement les frais d'entrée.
    expect(screen.getByText('+55.88')).toBeInTheDocument();
  });

  it("n'affiche pas l'avertissement quand les deux coïncident", () => {
    const sansEcart = { ...REEL, total_pnl: 17.6, total_entry_fees: 0 };
    render(<CostBreakdownCard result={sansEcart as BacktestResult} />);
    expect(screen.queryByText(/ne retranche pas les frais/)).not.toBeInTheDocument();
  });

  it('montre le funding comme un gain quand il est négatif', () => {
    const encaisse = { ...REEL, total_funding_cost: -12.5 };
    render(<CostBreakdownCard result={encaisse as BacktestResult} />);
    expect(screen.getByText(/encaissé/)).toBeInTheDocument();
  });

  it("ne rend rien sans décomposition — le cas des backtests d'avant L0", () => {
    const { container } = render(
      <CostBreakdownCard result={{ total_pnl: 10 } as BacktestResult} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
