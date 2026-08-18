'use client';

/**
 * Sélecteur de timeframe unifié (source : trading.timeframes).
 *
 * UX par défaut = **boutons** (pas une liste déroulante).
 * - mode single (défaut) : un seul TF actif
 * - mode multi : plusieurs TF (Lab optimiser, etc.)
 *
 * `TimeframeSelect` (dropdown) reste dispo pour formulaires très denses.
 */

import { cn } from '@/lib/utils';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';

export function TimeframeSelect({
  value,
  onChange,
  className,
  size = 'md',
  allowExtra,
  'aria-label': ariaLabel = 'Timeframe',
}: {
  value: string;
  onChange: (tf: string) => void;
  className?: string;
  size?: 'sm' | 'md';
  /** TF hors liste (ex. favori legacy) encore affiché. */
  allowExtra?: string[];
  'aria-label'?: string;
}) {
  const { timeframes } = useTradingTimeframes();
  const opts = Array.from(new Set([...timeframes, ...(allowExtra || [])]));

  return (
    <select
      aria-label={ariaLabel}
      value={opts.includes(value) ? value : (opts[0] || value)}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        'bg-card-hover border border-border rounded-md font-mono',
        size === 'sm' ? 'px-2 py-1.5 text-xs' : 'px-3 py-2 text-sm',
        className,
      )}
    >
      {opts.map((tf) => (
        <option key={tf} value={tf}>{tf}</option>
      ))}
    </select>
  );
}

/** Boutons TF — mode single (défaut) ou multi. */
export function TimeframeButtons({
  value,
  onChange,
  values,
  onChangeMulti,
  multi = false,
  className,
  size = 'md',
  allowExtra,
}: {
  /** Mode single. */
  value?: string;
  onChange?: (tf: string) => void;
  /** Mode multi. */
  values?: string[];
  onChangeMulti?: (tfs: string[]) => void;
  multi?: boolean;
  className?: string;
  size?: 'sm' | 'md';
  allowExtra?: string[];
}) {
  const { timeframes } = useTradingTimeframes();
  const opts = Array.from(new Set([...timeframes, ...(allowExtra || [])]));
  const selected = multi
    ? new Set(values || [])
    : new Set(value ? [value] : []);

  const toggle = (tf: string) => {
    if (multi && onChangeMulti) {
      const next = new Set(selected);
      if (next.has(tf)) {
        if (next.size <= 1) return; // au moins 1 TF
        next.delete(tf);
      } else {
        next.add(tf);
      }
      // conserver l'ordre de la config
      onChangeMulti(opts.filter((t) => next.has(t)));
      return;
    }
    onChange?.(tf);
  };

  return (
    <div
      className={cn('flex flex-wrap gap-1', className)}
      role={multi ? 'group' : 'radiogroup'}
      aria-label={multi ? 'Timeframes (multi)' : 'Timeframe'}
    >
      {opts.map((tf) => {
        const active = selected.has(tf);
        return (
          <button
            key={tf}
            type="button"
            onClick={() => toggle(tf)}
            aria-pressed={multi ? active : undefined}
            aria-checked={!multi ? active : undefined}
            role={multi ? 'button' : 'radio'}
            className={cn(
              'rounded-md font-mono border transition-colors',
              size === 'sm' ? 'px-2 py-1 text-[11px]' : 'px-2.5 py-1 text-xs',
              active
                ? 'bg-primary-500/15 text-primary-800 dark:text-primary-400 border-primary-500/40'
                : 'bg-card-hover text-muted border-border hover:border-border-hi',
            )}
          >
            {tf}
          </button>
        );
      })}
    </div>
  );
}
