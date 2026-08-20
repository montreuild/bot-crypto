import { describe, it, expect } from 'vitest';
import {
  equityValues, formatDrawdownPct, alphaPct, asPercentPoints, formatPctPoints,
} from '@/lib/backend-normalizers';

describe('equityValues', () => {
  it('lit un tableau de nombres (contrat backend)', () => {
    expect(equityValues([10000, 10050, 9940])).toEqual([10000, 10050, 9940]);
  });
  it('lit aussi {equity} si un jour le format change', () => {
    expect(equityValues([{ equity: 10 }, { equity: 12 }])).toEqual([10, 12]);
  });
  it('ignore le mapping p.equity sur un number (source du 0$)', () => {
    const broken = [10000, 10120].map((p: any) => p.equity);
    expect(broken.every((v) => v == null)).toBe(true);
    expect(equityValues([10000, 10120])[0]).toBe(10000);
  });
});

describe('formatDrawdownPct', () => {
  it('n\'applique pas ×100 à un DD déjà en %', () => {
    expect(formatDrawdownPct(-87.91)).toBe('-87.9%');
  });
  it('accepte un ratio -0.12 → -12.0%', () => {
    expect(formatDrawdownPct(-0.12)).toBe('-12.0%');
  });
});

describe('asPercentPoints', () => {
  it('ne multiplie pas un WR déjà en %', () => {
    expect(asPercentPoints(25.8)).toBe(25.8);
    expect(formatPctPoints(25.8)).toBe('25.8%');
  });
  it('convertit un ratio 0–1', () => {
    expect(asPercentPoints(0.258)).toBeCloseTo(25.8, 5);
  });
  it('évite 2580 % et 832 %', () => {
    expect(formatPctPoints(25.8)).not.toBe('2580.0%');
    expect(formatDrawdownPct(8.32)).toBe('8.3%');
  });
});

describe('alphaPct', () => {
  it('convertit un alpha en devise en % du capital', () => {
    expect(alphaPct(-6420.9, 10000)).toBeCloseTo(-64.209, 3);
  });
  it('préfère alpha_vs_bh s\'il est raisonnable', () => {
    expect(alphaPct(-6420.9, 10000, -12.5)).toBe(-12.5);
  });
});
