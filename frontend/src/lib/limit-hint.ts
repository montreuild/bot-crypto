/**
 * BT-012, OPT-005, RPL-006, CMP-001 — conversion bougies → durée humaine.
 *
 * Extrait du template Jinja2 `backtest.html:196-206` (`updateLimHint`).
 * Porté en TypeScript : un seul util partagé par Backtest, Optimizer,
 * Replay et Compare.
 */

const TF_MINUTES: Record<string, number> = {
  '1m': 1,
  '5m': 5,
  '15m': 15,
  '30m': 30,
  '1h': 60,
  '2h': 120,
  '4h': 240,
  '6h': 360,
  '12h': 720,
  '1d': 1440,
  '1w': 10080,
};

export function tfMinutes(tf: string): number {
  return TF_MINUTES[tf] ?? 60;
}

export interface LimitHint {
  text: string;
  /** 'green' = assez long, 'amber' = court, 'red' = trop court. */
  tone: 'green' | 'amber' | 'red';
}

export function limitHint(limit: number, tf: string): LimitHint {
  const tfMin = tfMinutes(tf);
  const hours = (limit * tfMin) / 60;
  const days = hours / 24;

  let text: string;
  if (days < 1) {
    text = `≈ ${Math.round(hours)}h de données`;
  } else if (days < 30) {
    text = `≈ ${Math.round(days)}j de données`;
  } else if (days < 90) {
    text = `≈ ${Math.round(days)}j de données`;
  } else if (days < 365) {
    text = `≈ ${Math.round(days)}j (~${Math.round(days / 30)} mois) de données`;
  } else {
    text = `≈ ${Math.round(days)}j (~${(days / 365).toFixed(1)} an) de données`;
  }

  const tone: LimitHint['tone'] = days < 30 ? 'amber' : days < 90 ? 'amber' : 'green';
  return { text, tone };
}

/**
 * OPT-005 — répartition IS/OOS pour l'optimiseur.
 * Convention 65/35 (cf. `optimizer_search.py:_OOS_FRACTION`).
 */
export interface IsOosHint {
  text: string;
  /** True si la demande dépasse le plafond OKX (8000 bougies 1h = ~333j). */
  capped: boolean;
}

export function isOosHint(limitPerTf: number, tf: string): IsOosHint {
  if (!limitPerTf || limitPerTf <= 0) {
    return { text: 'Auto — le backend décide selon le TF', capped: false };
  }
  const is = Math.round(limitPerTf * 0.65);
  const oos = limitPerTf - is;
  const tfMin = tfMinutes(tf);
  const isDays = Math.round((is * tfMin) / 60 / 24);
  const oosDays = Math.round((oos * tfMin) / 60 / 24);
  const capped = limitPerTf > 8000 && tfMin <= 60;
  return {
    text: `${tf} → IS ${is} (~${isDays}j) · OOS ${oos} (~${oosDays}j)${capped ? ' (max OKX)' : ''}`,
    capped,
  };
}

/**
 * RPL-006 — estimation du nombre de bougies à partir d'un nombre de mois.
 */
export function monthsToBougies(months: number, tf: string): number {
  const tfMin = tfMinutes(tf);
  // ~30.44 jours par mois, 24h, 60min
  const minutesPerMonth = 30.44 * 24 * 60;
  return Math.round((months * minutesPerMonth) / tfMin);
}
