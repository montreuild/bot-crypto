/**
 * Cycle de vie causal des entités SMC pendant un rejeu (ARCH-02).
 *
 * Une entité (order block, pool, FVG, structure) naît à une barre et meurt
 * parfois à une autre. Le rejeu ne doit montrer que ce qui existait à la barre
 * courante — sans quoi il donne à voir le futur.
 *
 * Fonctions pures : la vue les consomme, les tests les vérifient sans monter
 * de graphique.
 */

export interface ReplayTrade {
  entry_bar?: number;
  exit_bar?: number;
  entry_time?: number | string;
  exit_time?: number | string;
  [k: string]: unknown;
}

export interface StructureEvent {
  kind: 'BOS' | 'CHoCH';
  type: string;
  time: number;
  index: number | null;
}

const CLES_NAISSANCE = ['created_at', 'index', 'confirmed_at'];
const CLES_MORT = ['invalidated_at', 'swept_at', 'filled_at'];

function premierIndexFini(entity: unknown, cles: string[]): number | null {
  if (!entity || typeof entity !== 'object') return null;
  const e = entity as Record<string, unknown>;
  for (const k of cles) {
    if (typeof e[k] === 'number' && Number.isFinite(e[k])) return e[k] as number;
  }
  return null;
}

/** Barre de naissance, ou `null` si l'entité n'en déclare aucune. */
export function entityBarIndex(entity: unknown, fallbackKeys = CLES_NAISSANCE): number | null {
  return premierIndexFini(entity, fallbackKeys);
}

/** Barre de fin de vie (invalidation / sweep / remplissage). */
export function entityEndIndex(entity: unknown): number | null {
  return premierIndexFini(entity, CLES_MORT);
}

/** Sans information de cycle de vie, l'entité est toujours affichée. */
export function entityAliveAt(entity: unknown, currentIndex: number): boolean {
  const start = entityBarIndex(entity);
  if (start == null) return true;
  if (currentIndex < start) return false;
  const end = entityEndIndex(entity);
  return end == null || currentIndex < end;
}

export function aliveEntities<T>(list: T[] | undefined, currentIndex: number): T[] {
  return (list || []).filter((e) => entityAliveAt(e, currentIndex));
}

/** Les N dernières entités vivantes, plus récente en tête. */
export function recentAlive<T>(list: T[] | undefined, currentIndex: number, n = 12): T[] {
  return aliveEntities(list, currentIndex).slice(-n).reverse();
}

/**
 * Barres d'entrée et de sortie d'un trade de rejeu.
 *
 * L'API rend `entry_bar`/`exit_bar` (indices) mais d'anciennes réponses
 * portaient `entry_time`/`exit_time` avec un indice dedans — d'où le repli.
 */
export function tradeBars(t: ReplayTrade): { entry: number | null; exit: number | null } {
  const num = (a: unknown, b: unknown) =>
    typeof a === 'number' ? a : typeof b === 'number' ? b : null;
  return { entry: num(t.entry_bar, t.entry_time), exit: num(t.exit_bar, t.exit_time) };
}

export function tradeIsOpenAt(t: ReplayTrade, currentIndex: number): boolean {
  const { entry, exit } = tradeBars(t);
  return entry != null && currentIndex >= entry && (exit == null || currentIndex < exit);
}

export function openTradesAt(trades: ReplayTrade[] | undefined, currentIndex: number): ReplayTrade[] {
  return (trades || []).filter((t) => tradeIsOpenAt(t, currentIndex));
}

export function closedTradesAt(
  trades: ReplayTrade[] | undefined, currentIndex: number, n = 12,
): ReplayTrade[] {
  return (trades || [])
    .filter((t) => {
      const { exit } = tradeBars(t);
      return exit != null && currentIndex >= exit;
    })
    .slice(-n)
    .reverse();
}

/** Dernier événement de structure atteint à `currentIndex`, BOS et CHoCH confondus. */
export function lastStructureAt(
  structure: { bos?: any[]; choch?: any[] } | undefined, currentIndex: number,
): StructureEvent | null {
  const all: StructureEvent[] = [];
  const pousser = (kind: 'BOS' | 'CHoCH', list: any[] | undefined) => {
    for (const e of (list || [])) {
      all.push({ kind, type: e.type, time: e.time, index: entityBarIndex(e, ['index', 'created_at']) });
    }
  };
  pousser('BOS', structure?.bos);
  pousser('CHoCH', structure?.choch);
  return all
    .filter((s) => s.index == null || s.index <= currentIndex)
    .sort((a, b) => (a.index ?? 0) - (b.index ?? 0))
    .pop() ?? null;
}

/**
 * Bougies du rejeu, quelle que soit la forme rendue par l'API.
 *
 * `ohlcv` (colonnes) est la forme actuelle ; `candles` (liste d'objets) reste
 * servie par d'anciennes réponses.
 */
export function replayCandles(
  data: { ohlcv?: Record<string, number[]>; candles?: unknown[] } | undefined,
  clean: (t: number[], o: number[], h: number[], l: number[], c: number[]) => any[],
): any[] {
  const ohlcv = data?.ohlcv;
  if (ohlcv?.time?.length) {
    return clean(ohlcv.time || [], ohlcv.open || [], ohlcv.high || [],
                 ohlcv.low || [], ohlcv.close || []);
  }
  const candles = data?.candles;
  if (!Array.isArray(candles) || candles.length === 0) return [];
  const cols: Record<string, number[]> = { time: [], open: [], high: [], low: [], close: [] };
  for (const c of candles) {
    if (c == null || typeof c !== 'object') continue;
    for (const k of Object.keys(cols)) cols[k].push(Number((c as any)[k]));
  }
  return clean(cols.time, cols.open, cols.high, cols.low, cols.close);
}
