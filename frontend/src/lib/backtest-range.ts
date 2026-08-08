/**
 * QW-2 — Logique du mode « Plage de dates » du backtest.
 *
 * Isolée du composant pour être testable : le format des bornes renvoyées par
 * `/api/backtest/range` n'est pas homogène, et c'est le genre de détail qui
 * casse silencieusement un `<input type="date">` (il exige `yyyy-mm-dd`, et
 * ignore sans broncher toute valeur qu'il ne sait pas lire).
 *
 * Côté serveur, `CandleStore.stats()` construit ses bornes avec `str()` sur un
 * datetime polars, ce qui donne « 2024-01-01 23:00:00 » (séparateur ESPACE),
 * tronqué à 16 caractères. Mais la route peut aussi renvoyer de l'ISO avec un
 * « T ». On accepte donc les deux.
 */

/** Bornes disponibles en cache, telles que renvoyées par `/api/backtest/range`. */
export interface AvailableRange {
  from: string | null;
  to: string | null;
  bars: number;
  available: boolean;
}

/**
 * Ramène une date serveur au format `yyyy-mm-dd` attendu par `<input type="date">`.
 * Renvoie '' si la valeur est inutilisable — un input vide vaut mieux qu'un
 * input qui affiche une date fausse.
 */
export function toDateInputValue(raw: string | null | undefined): string {
  if (!raw) return '';
  // « 2024-01-01 23:00 », « 2024-01-01T23:00:00 », « 2024-01-01 » → « 2024-01-01 »
  const match = /^(\d{4}-\d{2}-\d{2})/.exec(raw.trim());
  return match ? match[1] : '';
}

export interface RangeValidation {
  ok: boolean;
  /** Message bloquant — le run ne doit pas partir. */
  error?: string;
  /** Message non bloquant — le run part, mais le résultat surprendra. */
  warning?: string;
}

/**
 * Valide la plage saisie avant de lancer le backtest.
 *
 * Distingue deux niveaux, parce qu'ils n'appellent pas la même réaction :
 * une plage incohérente est une erreur de saisie (on bloque), alors qu'une
 * plage qui déborde du cache est un fait à signaler (le backtest tournera,
 * simplement sur moins de données que demandé).
 */
export function validateDateRange(
  start: string,
  end: string,
  available?: AvailableRange | null,
): RangeValidation {
  if (!start && !end) {
    return { ok: false, error: 'Indiquez au moins une borne (début ou fin).' };
  }
  if (start && end && start > end) {
    return { ok: false, error: 'La date de début est postérieure à la date de fin.' };
  }

  if (available && !available.available) {
    return {
      ok: false,
      error: 'Aucune donnée en cache pour ce couple symbole / timeframe. '
        + 'Lancez un backfill depuis Données, ou utilisez le mode « Dernières bougies ».',
    };
  }

  if (available?.available) {
    const min = toDateInputValue(available.from);
    const max = toDateInputValue(available.to);
    // Plage entièrement hors cache → le backtest n'aurait aucune bougie, et le
    // serveur répondrait 400. Autant le dire ici, avec le motif exact.
    if ((max && start && start > max) || (min && end && end < min)) {
      return {
        ok: false,
        error: `Plage hors cache — données disponibles du ${min} au ${max}.`,
      };
    }
    const debordeAvant = Boolean(min && start && start < min);
    const debordeApres = Boolean(max && end && end > max);
    if (debordeAvant || debordeApres) {
      return {
        ok: true,
        warning: `Le cache ne couvre que ${min} → ${max} : le backtest sera tronqué à cette plage.`,
      };
    }
  }

  return { ok: true };
}

/** Nombre de jours couverts par la plage (inclusif), pour l'indication de durée. */
export function rangeDurationDays(start: string, end: string): number | null {
  if (!start || !end) return null;
  const a = Date.parse(`${start}T00:00:00Z`);
  const b = Date.parse(`${end}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return null;
  return Math.round((b - a) / 86_400_000) + 1;
}
