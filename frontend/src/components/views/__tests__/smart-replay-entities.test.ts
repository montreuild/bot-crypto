/**
 * ARCH-02 — le cycle de vie causal du rejeu, désormais testable.
 *
 * Ces règles vivaient dans `smart-replay-view.tsx` (744 lignes), inatteignables
 * sans monter un graphique. Elles décident de ce que le rejeu montre à chaque
 * barre : une erreur ici fait voir le futur.
 */
import { describe, expect, it } from 'vitest';

import {
  aliveEntities, closedTradesAt, entityAliveAt, entityBarIndex, entityEndIndex,
  lastStructureAt, openTradesAt, recentAlive, replayCandles, tradeBars,
} from '@/components/views/smart-replay-entities';

describe('entityBarIndex / entityEndIndex', () => {
  it('prend la première clé numérique finie, dans l’ordre déclaré', () => {
    expect(entityBarIndex({ created_at: 5, index: 9 })).toBe(5);
    expect(entityBarIndex({ index: 9, confirmed_at: 3 })).toBe(9);
    expect(entityBarIndex({ confirmed_at: 3 })).toBe(3);
  });

  it('ignore les valeurs non numériques ou non finies', () => {
    expect(entityBarIndex({ created_at: 'x', index: 4 })).toBe(4);
    expect(entityBarIndex({ created_at: NaN, index: 4 })).toBe(4);
    expect(entityBarIndex({ created_at: Infinity })).toBeNull();
  });

  it('rend null sur une entrée absente ou non-objet', () => {
    for (const v of [null, undefined, 3, 'x', {}]) expect(entityBarIndex(v)).toBeNull();
  });

  it('accepte une liste de clés explicite', () => {
    expect(entityBarIndex({ created_at: 1, index: 7 }, ['index'])).toBe(7);
  });

  it('lit les trois fins de vie', () => {
    expect(entityEndIndex({ invalidated_at: 2 })).toBe(2);
    expect(entityEndIndex({ swept_at: 3 })).toBe(3);
    expect(entityEndIndex({ filled_at: 4 })).toBe(4);
    expect(entityEndIndex({ created_at: 4 })).toBeNull();
  });
});

describe('entityAliveAt', () => {
  it('affiche toujours une entité sans information de cycle de vie', () => {
    expect(entityAliveAt({ level: 100 }, 0)).toBe(true);
    expect(entityAliveAt({ level: 100 }, 9999)).toBe(true);
  });

  it('ne montre rien avant la naissance', () => {
    expect(entityAliveAt({ created_at: 10 }, 9)).toBe(false);
    expect(entityAliveAt({ created_at: 10 }, 10)).toBe(true);
  });

  it('la barre de mort est exclue — l’entité n’existe plus à cet instant', () => {
    const e = { created_at: 5, swept_at: 8 };
    expect(entityAliveAt(e, 7)).toBe(true);
    expect(entityAliveAt(e, 8)).toBe(false);
    expect(entityAliveAt(e, 9)).toBe(false);
  });

  it('aliveEntities et recentAlive filtrent sur la même règle', () => {
    const liste = [
      { created_at: 0 }, { created_at: 5 }, { created_at: 5, filled_at: 6 },
    ];
    expect(aliveEntities(liste, 5)).toHaveLength(3);
    expect(aliveEntities(liste, 6)).toHaveLength(2);
    expect(aliveEntities(undefined, 3)).toEqual([]);
  });

  it('recentAlive rend les N dernières, plus récente en tête', () => {
    const liste = Array.from({ length: 20 }, (_, i) => ({ created_at: 0, id: i }));
    const out = recentAlive(liste, 5, 3) as Array<{ id: number }>;
    expect(out.map((e) => e.id)).toEqual([19, 18, 17]);
  });
});

describe('tradeBars', () => {
  it('préfère entry_bar / exit_bar', () => {
    expect(tradeBars({ entry_bar: 3, exit_bar: 8, entry_time: 99 }))
      .toEqual({ entry: 3, exit: 8 });
  });

  it('retombe sur entry_time / exit_time quand ce sont des nombres', () => {
    expect(tradeBars({ entry_time: 3, exit_time: 8 })).toEqual({ entry: 3, exit: 8 });
  });

  it('rend null quand la valeur n’est pas un nombre', () => {
    expect(tradeBars({ entry_time: '2024-01-01' })).toEqual({ entry: null, exit: null });
    expect(tradeBars({})).toEqual({ entry: null, exit: null });
  });
});

describe('openTradesAt / closedTradesAt', () => {
  const trades = [
    { id: 'a', entry_bar: 0, exit_bar: 5 },
    { id: 'b', entry_bar: 3 },              // jamais clôturé
    { id: 'c', entry_bar: 10, exit_bar: 12 },
    { id: 'd' },                            // sans barres
  ];

  it('ouvert = entré et pas encore sorti', () => {
    expect(openTradesAt(trades, 4).map((t) => t.id)).toEqual(['a', 'b']);
    expect(openTradesAt(trades, 5).map((t) => t.id)).toEqual(['b']);
    expect(openTradesAt(trades, 11).map((t) => t.id)).toEqual(['b', 'c']);
  });

  it('un trade sans barre d’entrée n’est jamais ouvert', () => {
    expect(openTradesAt(trades, 999).map((t) => t.id)).not.toContain('d');
  });

  it('clôturé = la barre de sortie est atteinte, plus récent en tête', () => {
    expect(closedTradesAt(trades, 4)).toEqual([]);
    expect(closedTradesAt(trades, 5).map((t) => t.id)).toEqual(['a']);
    expect(closedTradesAt(trades, 12).map((t) => t.id)).toEqual(['c', 'a']);
  });

  it('borne le nombre de trades clôturés rendus', () => {
    const nombreux = Array.from({ length: 30 }, (_, i) => ({ entry_bar: 0, exit_bar: 1, id: i }));
    expect(closedTradesAt(nombreux, 5, 4)).toHaveLength(4);
  });
});

describe('lastStructureAt', () => {
  const structure = {
    bos: [{ type: 'bullish', time: 100, index: 2 }, { type: 'bearish', time: 300, index: 8 }],
    choch: [{ type: 'bullish', time: 200, index: 5 }],
  };

  it('rend le dernier événement atteint, BOS et CHoCH confondus', () => {
    expect(lastStructureAt(structure, 4)).toMatchObject({ kind: 'BOS', index: 2 });
    expect(lastStructureAt(structure, 6)).toMatchObject({ kind: 'CHoCH', index: 5 });
    expect(lastStructureAt(structure, 99)).toMatchObject({ kind: 'BOS', index: 8 });
  });

  it('rend null avant tout événement', () => {
    expect(lastStructureAt(structure, 0)).toBeNull();
    expect(lastStructureAt(undefined, 5)).toBeNull();
  });

  it('un événement sans index reste visible — pas d’information, pas de filtre', () => {
    const sansIndex = { bos: [{ type: 'bullish', time: 1 }] };
    expect(lastStructureAt(sansIndex, 0)).toMatchObject({ kind: 'BOS', index: null });
  });
});

describe('replayCandles', () => {
  const clean = (t: number[], o: number[], h: number[], l: number[], c: number[]) =>
    t.map((time, i) => ({ time, open: o[i], high: h[i], low: l[i], close: c[i] }));

  it('lit la forme colonnes', () => {
    const out = replayCandles({
      ohlcv: { time: [1, 2], open: [10, 11], high: [12, 13], low: [9, 10], close: [11, 12] },
    }, clean);
    expect(out).toHaveLength(2);
    expect(out[1]).toEqual({ time: 2, open: 11, high: 13, low: 10, close: 12 });
  });

  it('retombe sur la forme liste d’objets', () => {
    const out = replayCandles({
      candles: [{ time: 1, open: 10, high: 12, low: 9, close: 11 }],
    }, clean);
    expect(out).toEqual([{ time: 1, open: 10, high: 12, low: 9, close: 11 }]);
  });

  it('les colonnes priment sur la liste quand les deux sont là', () => {
    const out = replayCandles({
      ohlcv: { time: [7], open: [1], high: [1], low: [1], close: [1] },
      candles: [{ time: 99, open: 1, high: 1, low: 1, close: 1 }],
    }, clean);
    expect(out[0].time).toBe(7);
  });

  it('rend une liste vide sans données exploitables', () => {
    expect(replayCandles(undefined, clean)).toEqual([]);
    expect(replayCandles({}, clean)).toEqual([]);
    expect(replayCandles({ candles: [] }, clean)).toEqual([]);
    expect(replayCandles({ ohlcv: { time: [] } }, clean)).toEqual([]);
  });

  it('ignore les entrées non-objet de la liste', () => {
    const out = replayCandles({
      candles: [null, { time: 1, open: 1, high: 1, low: 1, close: 1 }],
    } as any, clean);
    expect(out).toHaveLength(1);
  });
});
