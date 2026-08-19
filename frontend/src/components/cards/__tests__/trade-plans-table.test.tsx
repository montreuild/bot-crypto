import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TradePlansTable, RealizedTradesTable } from '@/components/cards/trade-plans-table';

describe('TradePlansTable collapse', () => {
  it('replie et déplie au clic sur le titre', () => {
    render(
      <TradePlansTable
        plans={[{ side: 'long', setup: 'ob', entry: 1, stop: 0.9, tp: 1.2, signal_time: 1 }]}
      />,
    );
    expect(screen.getByText('LONG')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /replier trades recommandés/i }));
    expect(screen.queryByText('LONG')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /déplier trades recommandés/i }));
    expect(screen.getByText('LONG')).toBeInTheDocument();
  });
});

describe('RealizedTradesTable collapse', () => {
  it('replie et déplie au clic sur le titre', () => {
    render(
      <RealizedTradesTable
        trades={[{
          side: 'short', setup: 'fvg', entry: 2, stop: 2.1, tp: 1.5,
          exit: 1.6, pnl: 1, signal_time: 1,
        }]}
      />,
    );
    expect(screen.getByText('SHORT')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /replier/i }));
    expect(screen.queryByText('SHORT')).not.toBeInTheDocument();
  });
});
