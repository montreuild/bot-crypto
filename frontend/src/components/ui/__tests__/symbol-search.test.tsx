import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

vi.mock('@/lib/api', () => ({
  api: {
    getDataStatus: vi.fn().mockResolvedValue({ datasets: [] }),
    getUniverses: vi.fn().mockResolvedValue({ universes: [] }),
    getUniverse: vi.fn(),
  },
}));

import {
  classifySymbol, collectSymbolOptions, SymbolSearchInput,
} from '@/components/ui/symbol-search';

describe('classifySymbol', () => {
  it('repère les paires crypto', () => {
    expect(classifySymbol('BTC/USDC')).toBe('crypto');
    expect(classifySymbol('ETH/USDT')).toBe('crypto');
  });
  it('repère les actions (suffixe bourse)', () => {
    expect(classifySymbol('AIR.PA')).toBe('equity');
    expect(classifySymbol('AAPL.US')).toBe('equity');
  });
  it('classe le reste en other', () => {
    expect(classifySymbol('XYZ')).toBe('other');
  });
});

describe('collectSymbolOptions', () => {
  it('fusionne cache OHLCV et univers sans doublon', () => {
    const opts = collectSymbolOptions({
      datasets: [{ symbol: 'BTC/USDC' }, { symbol: 'AIR.PA' }],
      universeMembers: [
        { symbol: 'BTC/USDC', name: 'Bitcoin' },
        { symbol: 'MC.PA', name: 'LVMH', universe: 'sbf120' },
      ],
    });
    const symbols = opts.map((o) => o.symbol);
    expect(symbols).toEqual(['AIR.PA', 'BTC/USDC', 'MC.PA']);
    expect(opts.find((o) => o.symbol === 'BTC/USDC')?.hint).toBe('Bitcoin');
    expect(opts.find((o) => o.symbol === 'AIR.PA')?.group).toBe('equity');
  });
});

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function W({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('SymbolSearchInput', () => {
  it('autorise une valeur absente de la liste', () => {
    const onChange = vi.fn();
    render(<SymbolSearchInput value="" onChange={onChange} />, { wrapper: wrapper() });
    const input = screen.getByRole('combobox', { name: /symbole/i });
    fireEvent.change(input, { target: { value: 'FOO/BAR' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('FOO/BAR');
  });
});
