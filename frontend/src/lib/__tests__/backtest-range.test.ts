import { describe, it, expect } from 'vitest';

import {
  toDateInputValue,
  validateDateRange,
  rangeDurationDays,
  type AvailableRange,
} from '@/lib/backtest-range';

const cache = (from: string, to: string, bars = 1000): AvailableRange => ({
  from, to, bars, available: true,
});

describe('toDateInputValue', () => {
  it('accepte le format renvoyé par CandleStore.stats (séparateur espace)', () => {
    // `str()` sur un datetime polars → « 2024-01-01 23:00:00 », tronqué à 16.
    expect(toDateInputValue('2024-01-01 23:00')).toBe('2024-01-01');
  });

  it('accepte aussi de l’ISO avec un T', () => {
    expect(toDateInputValue('2024-03-15T08:30:00')).toBe('2024-03-15');
  });

  it('laisse passer une date seule', () => {
    expect(toDateInputValue('2024-03-15')).toBe('2024-03-15');
  });

  it('renvoie une chaîne vide plutôt qu’une date fausse', () => {
    // <input type="date"> ignore silencieusement ce qu'il ne sait pas lire :
    // mieux vaut un champ vide qu'un champ qui semble rempli.
    expect(toDateInputValue(null)).toBe('');
    expect(toDateInputValue(undefined)).toBe('');
    expect(toDateInputValue('')).toBe('');
    expect(toDateInputValue('pas une date')).toBe('');
  });
});

describe('validateDateRange', () => {
  it('refuse une plage entièrement vide', () => {
    const v = validateDateRange('', '');
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/au moins une borne/i);
  });

  it('accepte une seule borne', () => {
    expect(validateDateRange('2024-01-01', '').ok).toBe(true);
    expect(validateDateRange('', '2024-01-01').ok).toBe(true);
  });

  it('refuse un début postérieur à la fin', () => {
    const v = validateDateRange('2024-06-01', '2024-01-01');
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/postérieure/i);
  });

  it('refuse quand le cache est vide', () => {
    const v = validateDateRange('2024-01-01', '2024-06-01', {
      from: null, to: null, bars: 0, available: false,
    });
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/backfill|cache/i);
  });

  it('refuse une plage entièrement postérieure au cache', () => {
    const v = validateDateRange('2025-01-01', '2025-06-01', cache('2024-01-01', '2024-12-31'));
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/hors cache/i);
  });

  it('refuse une plage entièrement antérieure au cache', () => {
    const v = validateDateRange('2020-01-01', '2020-06-01', cache('2024-01-01', '2024-12-31'));
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/hors cache/i);
  });

  it('avertit sans bloquer quand la plage déborde partiellement', () => {
    // Le backtest tournera : il sera simplement tronqué au cache. C'est un
    // fait à signaler, pas une erreur de saisie.
    const v = validateDateRange('2023-01-01', '2024-06-01', cache('2024-01-01', '2024-12-31'));
    expect(v.ok).toBe(true);
    expect(v.warning).toMatch(/tronqué/i);
    expect(v.error).toBeUndefined();
  });

  it('ne dit rien quand la plage est entièrement couverte', () => {
    const v = validateDateRange('2024-02-01', '2024-03-01', cache('2024-01-01', '2024-12-31'));
    expect(v).toEqual({ ok: true });
  });

  it('tolère un cache dont les bornes portent une heure', () => {
    const v = validateDateRange('2024-02-01', '2024-03-01', cache('2024-01-01 23:00', '2024-12-31 22:00'));
    expect(v.ok).toBe(true);
    expect(v.warning).toBeUndefined();
  });
});

describe('rangeDurationDays', () => {
  it('compte les bornes de façon inclusive', () => {
    expect(rangeDurationDays('2024-01-01', '2024-01-01')).toBe(1);
    expect(rangeDurationDays('2024-01-01', '2024-01-31')).toBe(31);
  });

  it('traverse un changement d’heure sans dériver', () => {
    // Passage à l'heure d'été en Europe le 31/03/2024 : un calcul en heure
    // locale renverrait 30,958… jour et un arrondi hasardeux.
    expect(rangeDurationDays('2024-03-01', '2024-04-30')).toBe(61);
  });

  it('renvoie null quand la durée n’a pas de sens', () => {
    expect(rangeDurationDays('', '2024-01-01')).toBeNull();
    expect(rangeDurationDays('2024-01-01', '')).toBeNull();
    expect(rangeDurationDays('2024-06-01', '2024-01-01')).toBeNull();
  });
});
