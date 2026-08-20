/**
 * BT-002, BT-017 — badges colorés pour les raisons de sortie des trades.
 *
 * Extrait du template Jinja2 `backtest.html:946-962` (`exitReasonBadge`).
 * Mapping exit_reason → { emoji, abbr, color }.
 */

export interface ExitReasonBadge {
  emoji: string;
  abbr: string;
  color: string;
}

const MAP: Record<string, ExitReasonBadge> = {
  take_profit: { emoji: '🎯', abbr: 'TP', color: '#047857' },
  stop_loss: { emoji: '🛑', abbr: 'SL', color: '#be123c' },
  trailing_stop: { emoji: '⤵', abbr: 'TS', color: '#6b21a8' },
  exit_after_bars: { emoji: '⏱', abbr: 'T', color: '#92400e' },
  end_of_data: { emoji: '⏹', abbr: 'FIN', color: '#475569' },
  p_dir_inversion: { emoji: '🔄', abbr: 'INV', color: '#92400e' },
  p_dir_drop: { emoji: '📉', abbr: 'DROP', color: '#92400e' },
  regime_exit_TD: { emoji: '🚪', abbr: 'TD', color: '#155e75' },
  regime_exit_choppy: { emoji: '🚪', abbr: 'CH', color: '#155e75' },
  regime_to_TD: { emoji: '🚪', abbr: 'TD↓', color: '#155e75' },
  p_dir_weak: { emoji: '⬇', abbr: 'WEAK', color: '#92400e' },
  to_TD: { emoji: '⬇', abbr: '↓TD', color: '#155e75' },
  manual_close: { emoji: '✋', abbr: 'MAN', color: '#475569' },
};

const FALLBACK: ExitReasonBadge = { emoji: '', abbr: '?', color: '#475569' };

export function exitReasonBadge(reason: string | null | undefined): ExitReasonBadge {
  if (!reason) return FALLBACK;
  return MAP[reason] ?? { emoji: '', abbr: reason.slice(0, 3).toUpperCase(), color: '#475569' };
}

/**
 * BT-001 — abbréviation du setup d'entrée, pour les markers du chart prix.
 * Extrait de `backtest.html:713` (mapping setup → texte court).
 */
export function setupAbbr(setup: string | null | undefined, side: 'long' | 'short'): string {
  if (!setup) return side === 'long' ? '↑E' : '↓E';
  const arrow = side === 'long' ? '↑' : '↓';
  return (
    setup
      .replace(/_/g, ' ')
      .replace('SIGNAL UP', `${arrow}SIG`)
      .replace('SHORT TD HIGH', '↓TDH')
      .replace('SHORT TD', '↓TD')
      .replace('SHORT CHOPPY', '↓CH')
      .replace('LONG RANGE STRICT', '↑RNG')
      .replace('LONG CHOPPY', '↑CH')
      // Pour les autres setups : 6 premiers caractères majuscules
      .slice(0, 6)
      .toUpperCase() || `${arrow}E`
  );
}
