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

  it("annonce l'état de chaque puce via aria-pressed", () => {
    render(
      <StrategyPicker strategies={STRATS} value={['breakout']} onChange={() => {}} />,
    );
    expect(screen.getByRole('button', { name: /breakout/ })).toHaveAttribute(
      'aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /trend_rider/ })).toHaveAttribute(
      'aria-pressed', 'false');
  });

  it('groupe les puces sous le libellé du compteur', () => {
    render(<StrategyPicker strategies={STRATS} value={[]} onChange={() => {}} />);
    expect(screen.getByRole('group', { name: /Stratégies/ })).toBeInTheDocument();
  });

  it('porte le badge ML et les TF dans le nom accessible, pas dans un title inerte', () => {
    render(
      <StrategyPicker
        strategies={STRATS}
        value={['supertrend_macd']}
        onChange={() => {}}
        spaces={SPACES}
        selectedTfs={['15m']}
      />,
    );
    const nom = screen.getByRole('button', { name: /supertrend_macd/ })
      .getAttribute('aria-label') ?? '';
    expect(nom).toMatch(/^supertrend_macd/);   // WCAG 2.5.3 : le texte visible d'abord
    expect(nom).toContain('ML');
    expect(nom).toContain('1h, 4h');
    expect(nom).toContain('pas recommandé');
    expect(nom).toContain('sélectionnée');
  });

  it('garde le texte visible comme nom des actions groupées', () => {
    // Un aria-label qui remplace « Toutes » casserait la commande vocale.
    render(<StrategyPicker strategies={STRATS} value={[]} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Toutes' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Aucune' })).toBeInTheDocument();
  });
});
