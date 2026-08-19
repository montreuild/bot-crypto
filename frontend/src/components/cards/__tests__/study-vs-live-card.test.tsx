import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StudyVsLiveCard } from '@/components/cards/study-vs-live-card';

const runs = {
  reference: {
    initial_capital: 1000, total_trades: 4, total_pnl: 10, pnl_pct: 1, max_drawdown: 2,
  },
  live: {
    initial_capital: 90, total_trades: 4, total_pnl: 0.9, pnl_pct: 1, max_drawdown: 2,
  },
  ecart_pnl_pct: 0,
};

describe('StudyVsLiveCard', () => {
  it('ne plante pas si risk_amount est absent (payload dual-pass incomplet)', () => {
    render(
      <StudyVsLiveCard
        runs={runs}
        envelope={{
          venue: 'margin-isolated',
          symbol: 'BTC/USDC',
          currency: 'USDC',
          slot_envelope: 90,
          weight: 0.3,
          trade_risk_pct: 0.025,
        }}
      />,
    );
    expect(screen.getByText(/Étude vs Réel/)).toBeTruthy();
    expect(screen.getByText(/risque par trade/)).toBeTruthy();
    expect(screen.getByText(/2\.25/)).toBeTruthy();
  });
});
