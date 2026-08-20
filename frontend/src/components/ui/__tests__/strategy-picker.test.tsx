import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StrategyPicker } from '@/components/ui/strategy-picker';

const STRATS = ['trend_rider', 'breakout', 'supertrend_macd'];

describe('StrategyPicker', () => {
  it('n\'a aucune stratégie sélectionnée par défaut', () => {
    render(<StrategyPicker strategies={STRATS} value={[]} onChange={() => {}} />);
    expect(screen.getByText(/0\/3/)).toBeInTheDocument();
  });

  it('Toutes sélectionne la liste complète', () => {
    const onChange = vi.fn();
    render(<StrategyPicker strategies={STRATS} value={[]} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Toutes' }));
    expect(onChange).toHaveBeenCalledWith(STRATS);
  });

  it('Aucune vide la sélection', () => {
    const onChange = vi.fn();
    render(<StrategyPicker strategies={STRATS} value={STRATS} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Aucune' }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('le clic sur une puce bascule la sélection', () => {
    const onChange = vi.fn();
    render(<StrategyPicker strategies={STRATS} value={['breakout']} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /trend_rider/ }));
    expect(onChange).toHaveBeenCalledWith(['breakout', 'trend_rider']);
  });

  it('affiche le badge ML et les TF recommandés depuis spaces', () => {
    render(
      <StrategyPicker
        strategies={STRATS}
        value={[]}
        onChange={() => {}}
        spaces={{ supertrend_macd: { is_ml: true, recommended_tfs: ['1h', '4h'], timeframes: ['1h'], params: {}, n_combos: 1 } }}
      />,
    );
    expect(screen.getByText('ML')).toBeInTheDocument();
    expect(screen.getByText('1h')).toBeInTheDocument();
  });
});
