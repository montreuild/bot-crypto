import { describe, it, expect } from 'vitest';
import {
  formatMoney,
  formatUSD,
  quoteCurrency,
  inferQuoteFromSymbol,
  formatPct,
  formatNumber,
  formatCompact,
  formatTime,
  formatDateTime,
  timeAgo,
  pnlColor,
  pnlBgColor,
  parseSlotKey,
  errorMessage,
  lifecycleStyle,
} from '@/lib/utils';

describe('formatMoney', () => {
  it('formate en USD par défaut', () => {
    expect(formatMoney(1234.5)).toMatch(/1[\s\u00a0\u202f]234,50/);
  });

  it('accepte EUR', () => {
    const s = formatMoney(1234.5, 'EUR');
    expect(s).toMatch(/1[\s\u00a0\u202f]234,50/);
    expect(s).toMatch(/€|EUR/);
  });

  it('mappe USDC vers USD', () => {
    expect(formatMoney(10, 'USDC')).toBe(formatMoney(10, 'USD'));
  });

  it('formatUSD reste un alias', () => {
    expect(formatUSD(10)).toBe(formatMoney(10, 'USD'));
  });
});

describe('quoteCurrency', () => {
  it('lit cost_model.quote_currency', () => {
    expect(quoteCurrency({ cost_model: { quote_currency: 'EUR' } })).toBe('EUR');
  });

  it('lit envelope.currency', () => {
    expect(quoteCurrency({ envelope: { currency: 'EUR' } })).toBe('EUR');
  });

  it('lit RiskVenue.currency (envelope est un montant)', () => {
    expect(quoteCurrency({ currency: 'EUR', envelope: 1000 })).toBe('EUR');
  });

  it('infère EUR depuis un ticker .PA', () => {
    expect(inferQuoteFromSymbol('BNP.PA')).toBe('EUR');
    expect(quoteCurrency('AIR.PA')).toBe('EUR');
  });

  it('infère le quote d\'une paire', () => {
    expect(quoteCurrency('BTC/EUR')).toBe('EUR');
    expect(quoteCurrency('BTC/USDC')).toBe('USD');
  });

  it('replie sur USD', () => {
    expect(quoteCurrency(null)).toBe('USD');
    expect(quoteCurrency({})).toBe('USD');
  });
});

describe('formatPct / formatNumber / formatCompact', () => {
  it('préfixe le signe positif', () => {
    expect(formatPct(12.5)).toMatch(/\+12,50\s%/);
    expect(formatPct(-1, 1)).toMatch(/-1,0\s%/);
  });

  it('fixe les décimales', () => {
    expect(formatNumber(1.2345, 2)).toBe('1,23');
  });

  it('compacte les grands nombres', () => {
    expect(formatCompact(1_200_000)).toMatch(/1,2/);
  });
});

describe('dates', () => {
  it('rend — pour une valeur vide', () => {
    expect(formatTime(null)).toBe('—');
    expect(formatDateTime(undefined)).toBe('—');
  });

  it('formate une ISO', () => {
    expect(formatTime('2024-01-01T12:30:00Z')).not.toBe('—');
    expect(formatDateTime('2024-01-01T12:30:00Z')).not.toBe('—');
  });

  it('timeAgo pour une date récente', () => {
    const now = new Date().toISOString();
    expect(timeAgo(now)).toMatch(/s|min|jamais/);
  });
});

describe('pnl + slot + erreurs', () => {
  it('pnlColor selon le signe', () => {
    expect(pnlColor(1)).toBe('text-profit');
    expect(pnlColor(-1)).toBe('text-loss');
    expect(pnlColor(0)).toBe('text-neutral-pnl');
    expect(pnlBgColor(1)).toBe('bg-profit-dim');
  });

  it('parse un slot_key 3 parties', () => {
    expect(parseSlotKey('trend::1h::BTC/USDC')).toEqual({
      strategy: 'trend', tf: '1h', symbol: 'BTC/USDC',
    });
  });

  it('errorMessage extrait le message', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom');
    expect(errorMessage('x')).toBe('x');
    expect(errorMessage(null, 'fb')).toBe('fb');
  });

  it('lifecycleStyle a un repli', () => {
    expect(lifecycleStyle('actif').label).toBe('Actif');
    expect(lifecycleStyle('inconnu').label).toBe('Candidat');
  });
});
