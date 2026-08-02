/**
 * Le contexte facturé doit être LISIBLE, pas seulement présent dans le payload.
 *
 * Les deux charges utiles ci-dessous sont copiées de la sortie réelle de
 * `app/core/execution.py::cost_model` sur la config livrée (BTC/USDC 1h et
 * AIR.PA 1d), pas inventées : c'est ce qui garantit que le composant affiche
 * la forme que le backend produit vraiment.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CostModelCard } from '@/components/cards/cost-model-card';
import type { CostModel } from '@/types';

const CRYPTO: CostModel = {
  venue: 'margin-isolated', market_type: 'margin', margin_mode: 'isolated',
  max_leverage: 1, asset_class: 'crypto', quote_currency: 'USDC', exchange: 'okx',
  calendar: '', can_execute: true, allow_short: true,
  fee_rate_taker: 0.001, fee_rate_maker: 0.0008, fee_pct_override: null,
  fee_fixed: 0, fee_min: 0, transaction_tax_pct: 0, tax_on_buy_only: true,
  borrows: true, borrow_rate_daily: 0.00072, borrow_periods_per_day: 24,
  borrow_rate_annual: 0.3005614529016254,
  spread_pct: 0.0005, slippage_model: 'static', slippage_k: 1,
  partial_fill_pct: 0.95, fractional: true, lot_size: 0, tick_size: 0,
  min_notional: 0, max_notional_pct: 0.2,
};

const ACTION: CostModel = {
  venue: 'euronext-paper', market_type: 'spot', margin_mode: null,
  max_leverage: 1, asset_class: 'equity', quote_currency: 'EUR',
  exchange: 'euronext', calendar: 'XPAR', can_execute: false, allow_short: false,
  fee_rate_taker: 0.001, fee_rate_maker: 0.001, fee_pct_override: 0.001,
  fee_fixed: 0, fee_min: 2, transaction_tax_pct: 0.004, tax_on_buy_only: true,
  borrows: false, borrow_rate_daily: 0, borrow_periods_per_day: 24,
  borrow_rate_annual: 0,
  spread_pct: 0.0005, slippage_model: 'static', slippage_k: 1,
  partial_fill_pct: 0.95, fractional: false, lot_size: 0, tick_size: 0.001,
  min_notional: 200, max_notional_pct: 0.2,
};

describe('CostModelCard', () => {
  it('annonce le marché et le levier en crypto', () => {
    render(<CostModelCard model={CRYPTO} />);
    expect(screen.getByText('margin / isolated')).toBeInTheDocument();
    expect(screen.getByText('levier ×1')).toBeInTheDocument();
    expect(screen.getByText('crypto / USDC')).toBeInTheDocument();
    expect(screen.getByText('margin-isolated')).toBeInTheDocument();
  });

  it('détaille l’emprunt quand il y en a, en journalier ET en annuel', () => {
    render(<CostModelCard model={CRYPTO} />);
    // Un taux journalier ne parle pas ; l'équivalent annuel composé, si.
    expect(screen.getByText('0.0720 %')).toBeInTheDocument();
    expect(screen.getByText('30.1 %')).toBeInTheDocument();
  });

  it('dit explicitement « aucun » emprunt sur une venue spot', () => {
    // Une ligne absente se lirait comme un oubli, pas comme une information.
    render(<CostModelCard model={ACTION} />);
    expect(screen.getByText(/aucun \(spot\)/)).toBeInTheDocument();
  });

  it('affiche les coûts propres aux actions', () => {
    render(<CostModelCard model={ACTION} />);
    expect(screen.getByText('Plancher courtage')).toBeInTheDocument();
    expect(screen.getByText('2.00')).toBeInTheDocument();
    expect(screen.getByText('Taxe transaction (achat)')).toBeInTheDocument();
    expect(screen.getByText('0.400 %')).toBeInTheDocument();
    expect(screen.getByText('entière')).toBeInTheDocument();
    expect(screen.getByText('200.00')).toBeInTheDocument();
    expect(screen.getByText('XPAR')).toBeInTheDocument();
    expect(screen.getByText('data-only')).toBeInTheDocument();
    expect(screen.getByText('interdite')).toBeInTheDocument();
  });

  it('masque en crypto les lignes propres aux actions', () => {
    // « plancher 0.00 » ou « taxe 0.000 % » seraient du bruit sans information.
    render(<CostModelCard model={CRYPTO} />);
    expect(screen.queryByText('Plancher courtage')).not.toBeInTheDocument();
    expect(screen.queryByText(/Taxe transaction/)).not.toBeInTheDocument();
    expect(screen.queryByText('Notionnel min')).not.toBeInTheDocument();
  });

  it('ne rend rien sans modèle (backtest ancien, payload sans cost_model)', () => {
    const { container } = render(<CostModelCard model={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
