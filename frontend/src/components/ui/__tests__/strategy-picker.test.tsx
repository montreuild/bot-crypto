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

  const SPACES = {
    supertrend_macd: {
      is_ml: true,
      recommended_tfs: ['1h', '4h'],
      timeframes: ['1h'],
      params: {},
      n_combos: 1,
    },
  };

  it('affiche le badge ML depuis spaces', () => {
    render(
      <StrategyPicker strategies={STRATS} value={[]} onChange={() => {}} spaces={SPACES} />,
    );
    expect(screen.getByText('ML')).toBeInTheDocument();
  });

  it("n'affiche pas les TF recommandés quand la sélection les respecte", () => {
    // Les badges de TF ne sont montrés que lorsqu'ils servent à quelque chose,
    // c'est-à-dire en présence d'un avertissement (`hasWarn`) : les afficher en
    // permanence saturait la puce sans rien apprendre.
    render(
      <StrategyPicker
        strategies={STRATS}
        value={['supertrend_macd']}
        onChange={() => {}}
        spaces={SPACES}
        selectedTfs={['1h']}
      />,
    );
    expect(screen.queryByText('4h')).not.toBeInTheDocument();
    expect(screen.queryByText('⚠')).not.toBeInTheDocument();
  });

  it('affiche les TF recommandés et un avertissement sur un TF non recommandé', () => {
    render(
      <StrategyPicker
        strategies={STRATS}
        value={['supertrend_macd']}
        onChange={() => {}}
        spaces={SPACES}
        selectedTfs={['15m']}
      />,
    );
    expect(screen.getByText('⚠')).toBeInTheDocument();
    expect(screen.getByText('1h')).toBeInTheDocument();
    expect(screen.getByText('4h')).toBeInTheDocument();
  });
});
