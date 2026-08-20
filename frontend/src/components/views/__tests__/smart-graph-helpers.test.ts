import { describe, it, expect } from 'vitest';
import { resolveSignalTimestamp } from '@/components/views/smart-graph-helpers';

describe('resolveSignalTimestamp', () => {
  const times = [100, 200, 300, 400];

  it('prend la bougie exacte', () => {
    expect(resolveSignalTimestamp(times, 200)).toBe(200);
  });
  it('prend la première bougie ≥ signal', () => {
    expect(resolveSignalTimestamp(times, 250)).toBe(300);
  });
  it('retombe sur la dernière si signal après la série', () => {
    expect(resolveSignalTimestamp(times, 999)).toBe(400);
  });
  it('retombe sur la dernière sans signal', () => {
    expect(resolveSignalTimestamp(times, null)).toBe(400);
  });
  it('vide → null', () => {
    expect(resolveSignalTimestamp([], 100)).toBeNull();
  });
});
